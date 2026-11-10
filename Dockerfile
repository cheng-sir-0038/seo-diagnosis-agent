# 分段构建
FROM python:3.11-slim AS builder

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY src/ src/

# -------------------------------------------------------
# python-seo-analyzer 独立依赖环境，避免与主项目 LangChain 1.x 冲突
FROM python:3.11-slim AS pyseo-builder

COPY requirements-pyseoanalyzer.txt /tmp/requirements-pyseoanalyzer.txt
RUN python -m venv /opt/pyseoanalyzer \
    && /opt/pyseoanalyzer/bin/pip install --no-cache-dir -r /tmp/requirements-pyseoanalyzer.txt

# -------------------------------------------------------
# 运行阶段
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /app /app
COPY --from=pyseo-builder /opt/pyseoanalyzer /opt/pyseoanalyzer

ENV PYTHON_SEO_ANALYZER_ENABLED=true \
    PYTHON_SEO_ANALYZER_PYTHON=/opt/pyseoanalyzer/bin/python

# 创建数据目录
RUN mkdir -p /app/data

EXPOSE 8000

# 生产环境使用 uvicorn 不加 --reload
CMD ["python", "-m", "uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]
