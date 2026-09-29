def run(page, base, shots, errors):
    """Step 12: the built-in validation cases are listed, run through the API and shown hand-vs-software with PASS chips."""
    page.click("#nav [data-step=validation]")
    page.wait_for_selector("text=cases pass", timeout=60000)
    page.wait_for_timeout(600)
    page.screenshot(path=f"{shots}/v_validation.png", full_page=True)
    chips = page.locator(".chip.pass").count()
    fails = page.locator(".chip.fail").count()
    print("PASS chips:", chips, " FAIL chips:", fails)
    assert fails == 0 and chips >= 55, (chips, fails)
    assert "5/5 cases pass" in page.inner_text("main") or "5/5 cases pass" in page.content()
