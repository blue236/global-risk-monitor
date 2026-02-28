from app.main import templates


def test_index_template_has_chart_lightbox_for_all_cards():
    html = templates.get_template("index.html").render(
        request=None,
        triggers=[],
        last_refresh=None,
        last_errors=None,
        auth_enabled=False,
        schedule={
            "timezone": "Europe/Berlin",
            "refresh_cron": "0 7 * * *",
            "report_cron": "5 7 * * *",
        },
    )

    assert 'id="chartLightbox"' in html
    assert 'id="chart_lightbox_canvas"' in html
    assert html.count('class="card chart-card"') >= 5

    # User-called-out charts should be supported explicitly.
    assert 'data-chart-canvas="chart_hy"' in html
    assert 'data-chart-canvas="chart_qqq"' in html

    # Accessibility and close interactions in JS.
    assert "setAttribute('tabindex', '0')" in html
    assert "e.key === 'Escape'" in html
