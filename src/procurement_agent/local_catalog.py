"""Local product catalogue using the Taobao Alliance material search JSON format."""

from __future__ import annotations

import re
from collections.abc import Iterator
from copy import deepcopy
from functools import cache
from hashlib import blake2s
from itertools import chain, product
from typing import NamedTuple
from unicodedata import normalize

LOCAL_CATALOG_SOURCE = "local_product_catalog"
LOCAL_CATALOG_LABEL = "本地商品库"
_RESPONSE = "tbk_dg_material_optional_upgrade_response"
_TERMS = re.compile(
    r"[a-z][a-z0-9]*|[0-9]+(?:\.[0-9]+)?(?:[a-z]+|[个只件支盒瓶包张抽本页卷条根号位粒])?|[\u4e00-\u9fff]+"
)
_UNIT_NAMES = {"毫升": "ml", "升": "l", "千克": "kg", "公斤": "kg", "克": "g",
               "毫米": "mm", "厘米": "cm", "米": "m"}
_CHINESE_UNIT = re.compile(r"(?<=[0-9])\s*(" + "|".join(_UNIT_NAMES) + ")")
_UNIT_SPACE = re.compile(r"(?<=\d)\s+(?=(?:ml|kg|cm|mm|[lgm])(?![a-z0-9]))")


class _Family(NamedTuple):
    category: str
    name: str
    aliases: str
    unit: str
    pack_sizes: tuple[int, int]
    specs: tuple[tuple[str, int], ...]


# Price points are in fen per unit. Packaging and seller prices vary independently.
_FAMILIES = (
    _Family("餐饮用品", "一次性纸杯", "纸杯 水杯 茶水杯 一次性杯子", "只", (50, 100),
            (("150ml", 9), ("180ml", 11), ("200ml", 14), ("250ml", 17))),
    _Family("餐饮用品", "一次性餐盒", "饭盒 打包盒 便当盒", "只", (25, 50),
            (("500ml", 37), ("650ml", 45), ("750ml", 55), ("1000ml", 79))),
    _Family("餐饮用品", "纸吸管", "吸管 饮料吸管", "支", (50, 100),
            (("15cm", 8), ("18cm", 9), ("21cm", 12), ("23cm", 14))),
    _Family("包装耗材", "牛皮纸礼袋", "礼品袋 手提袋 纸袋", "个", (10, 25),
            (("16×8×22cm", 61), ("21×11×27cm", 86),
             ("26×12×32cm", 110), ("32×16×40cm", 158))),
    _Family("日用清洁", "原木抽纸", "纸巾 面巾纸 抽纸", "包", (3, 6),
            (("100抽", 265), ("120抽", 320), ("150抽", 380), ("180抽", 450))),
    _Family("日用清洁", "加厚垃圾袋", "垃圾袋 清洁袋", "只", (30, 90),
            (("40×45cm", 9), ("45×50cm", 12), ("50×60cm", 17), ("60×80cm", 25))),
    _Family("日用清洁", "纤维清洁抹布", "厨房抹布 清洁布 洗碗布", "条", (3, 6),
            (("20×20cm", 118), ("25×25cm", 150), ("30×30cm", 185), ("30×40cm", 239))),
    _Family("日用清洁", "泡沫洗手液", "洗手液 清洁洗手", "瓶", (1, 2),
            (("100ml", 580), ("250ml", 980), ("300ml", 1260), ("500ml", 1680))),
    _Family("日用清洁", "柑橘洗洁精", "洗洁精 餐具清洁剂", "瓶", (1, 2),
            (("500g", 710), ("1kg", 1190), ("1.5kg", 1690), ("2kg", 2150))),
    _Family("办公耗材", "复印纸", "复印纸 打印纸 办公用纸", "包", (1, 5),
            (("A4 70g 500张", 1730), ("A4 80g 500张", 2100),
             ("A3 70g 500张", 3380), ("A3 80g 500张", 4160))),
    _Family("办公文具", "按动中性笔", "中性笔 水笔 签字笔", "支", (6, 12),
            (("0.35mm", 129), ("0.5mm", 111), ("0.7mm", 124), ("1.0mm", 145))),
    _Family("办公文具", "线装笔记本", "笔记本 记事本 本子", "本", (2, 5),
            (("A5 48页", 330), ("A5 80页", 590), ("B5 48页", 460), ("B5 80页", 720))),
    _Family("办公文具", "资料文件夹", "文件夹 文件册 资料册", "本", (1, 3),
            (("A4 20页", 590), ("A4 40页", 840), ("A4 60页", 1130), ("A4 80页", 1470))),
    _Family("办公用品", "透明亚克力桌牌", "桌牌 台卡 桌面标牌", "个", (2, 6),
            (("A6", 470), ("A5", 690), ("A4", 1100), ("100×200mm", 730))),
    _Family("办公耗材", "不干胶标签贴", "标签贴 标签纸 价签", "卷", (1, 3),
            (("20×30mm 500张", 420), ("30×40mm 500张", 650),
             ("40×60mm 300张", 750), ("50×70mm 300张", 1040))),
    _Family("包装耗材", "透明封箱胶带", "封箱胶带 透明胶 包装胶带", "卷", (3, 6),
            (("24mm×50m", 185), ("36mm×50m", 255),
             ("48mm×60m", 430), ("60mm×60m", 560))),
    _Family("包装耗材", "三层瓦楞快递纸箱", "快递纸箱 纸盒 包装箱", "个", (10, 25),
            (("15×10×8cm", 48), ("22×16×12cm", 84),
             ("28×20×16cm", 130), ("35×25×20cm", 195))),
    _Family("包装耗材", "尼龙理线扎带", "扎带 绑线带 理线带", "根", (50, 100),
            (("100mm", 7), ("150mm", 10), ("200mm", 15), ("250mm", 19))),
    _Family("数码配件", "USB-C编织数据线", "数据线 充电线 type-c USB-C", "根", (1, 2),
            (("0.5m", 970), ("1m", 1290), ("1.5m", 1550), ("2m", 1890))),
    _Family("数码配件", "无线鼠标", "鼠标 办公鼠标", "个", (1, 2),
            (("便携款", 2590), ("静音款", 3490), ("蓝牙双模款", 5290), ("充电款", 6590))),
    _Family("数码配件", "织面鼠标垫", "鼠标垫 桌垫 办公桌垫", "张", (1, 2),
            (("22×18cm", 570), ("25×21cm", 790), ("30×25cm", 1050), ("80×30cm", 1960))),
    _Family("家用电器", "带开关电源插座", "插座 插排 排插", "个", (1, 2),
            (("3位 1.5m", 1990), ("4位 1.8m", 2780), ("5位 3m", 3990), ("6位 5m", 5680))),
    _Family("数码配件", "碱性电池", "电池 干电池 AA AAA", "粒", (4, 8),
            (("5号 AA", 145), ("7号 AAA", 136), ("2号 C", 520), ("1号 D", 770))),
    _Family("家居收纳", "透明收纳盒", "收纳盒 整理盒 储物盒", "个", (1, 3),
            (("1L", 520), ("2L", 740), ("5L", 1420), ("10L", 2590))),
    _Family("厨房用品", "PE抽取式保鲜袋", "保鲜袋 食品袋 分装袋", "盒", (1, 3),
            (("16×20cm 100只", 480), ("20×25cm 100只", 630),
             ("25×35cm 80只", 750), ("30×40cm 80只", 960))),
)

# Store name, marketplace type, list-price multiplier, and price after discounts.
_SELLERS = (
    ("拾物{category}专营店", 1, 104, 94),
    ("禾与{category}旗舰店", 1, 110, 95),
    ("松里{category}商行", 0, 95, 97),
    ("简集{category}用品店", 0, 100, 96),
    ("晨栖{category}优选店", 0, 102, 92),
)


def _search_text(value: str) -> str:
    with_units = _CHINESE_UNIT.sub(
        lambda match: _UNIT_NAMES[match[1]], normalize("NFKC", value).casefold()
    )
    return _UNIT_SPACE.sub("", with_units)


def _search_pattern(term: str) -> re.Pattern:
    escaped = re.escape(term)
    return re.compile(rf"(?<![\d.]){escaped}(?![a-z0-9.])" if term[0].isdigit() else escaped)


def _yuan(fen: int) -> str:
    whole, cents = divmod(fen, 100)
    return f"{whole}.{cents:02d}"


def _family_rows(family: _Family) -> Iterator[tuple[str, dict]]:
    for (spec, unit_price), store, quantity in product(family.specs, _SELLERS, family.pack_sizes):
        name, user_type, markup, price_percent = store
        title = f"{family.name} {spec} {quantity}{family.unit}装"
        shop = name.format(category=family.category)
        offer_hash = blake2s(f"{title}|{shop}".encode(), digest_size=10).hexdigest()
        reserve = (unit_price * quantity * markup + 50) // 100
        final_price = (reserve * price_percent + 50) // 100
        yield _search_text(f"{title} {family.aliases} {family.category}"), {
            "item_id": offer_hash,
            "item_basic_info": {
                "title": title,
                "short_title": f"{family.name} {spec}",
                "category_name": family.name,
                "level_one_category_name": family.category,
                "user_type": user_type,
                "shop_title": shop,
                "volume": 12 + int(offer_hash[:6], 16) % 1200,
            },
            "price_promotion_info": {
                "reserve_price": _yuan(reserve),
                "final_promotion_price": _yuan(final_price),
            },
            "publish_info": {},
        }


@cache
def _catalog() -> tuple[tuple[str, dict], ...]:
    return tuple(chain.from_iterable(_family_rows(family) for family in _FAMILIES))


def material_search_response(
    query: str, *, page: int = 1, page_size: int = 5, tmall_only: bool = False
) -> dict:
    if page < 1 or not 1 <= page_size <= 100:
        raise ValueError("page must be positive and page_size must be between 1 and 100")
    terms = [_search_pattern(term) for term in _TERMS.findall(_search_text(query))]
    matches = [
        row
        for searchable, row in _catalog()
        if (not tmall_only or row["item_basic_info"]["user_type"] == 1)
        and all(term.search(searchable) for term in terms)
    ]
    start = (page - 1) * page_size
    return {
        _RESPONSE: {
            "total_results": len(matches),
            "result_list": {"map_data": deepcopy(matches[start : start + page_size])},
        }
    }
