from pathlib import Path


def test_chart_lightbox_modal_size_is_expanded():
    css = Path("app/static/styles.css").read_text(encoding="utf-8")

    assert "width:min(1600px,98vw)" in css
    assert "height:min(94vh,1040px)" in css
    assert "min-height:720px" in css
