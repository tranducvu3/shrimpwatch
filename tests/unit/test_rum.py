from app.api.routers.control import rum_snippet

APP_ID = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"
TOKEN = "pub" + "0123456789abcdef" * 2


def test_rum_is_off_unless_both_ids_are_set_and_well_formed():
    assert rum_snippet("", "", "datadoghq.com", "dev") == ""
    assert rum_snippet(APP_ID, "", "datadoghq.com", "dev") == ""
    assert rum_snippet(APP_ID, 'pub"</script><script>alert(1)', "datadoghq.com", "dev") == ""
    assert rum_snippet(APP_ID, TOKEN, "unknown.example", "dev") == ""


def test_rum_loads_the_regional_sdk_and_traces_the_panels_api_calls():
    snippet = rum_snippet(APP_ID, TOKEN, "ap2.datadoghq.com", "development")
    assert "https://www.datadoghq-browser-agent.com/ap2/v6/datadog-rum.js" in snippet
    assert f'"applicationId": "{APP_ID}"' in snippet and '"site": "ap2.datadoghq.com"' in snippet
    assert "allowedTracingUrls: [window.location.origin]" in snippet
