from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "frontend" / "vite.config.ts"


def test_vite_cache_is_worktree_local_and_react_is_deduped() -> None:
    source = CONFIG.read_text(encoding="utf-8")

    assert "cacheDir: '.vite'" in source
    assert "dedupe: ['react', 'react-dom']" in source
