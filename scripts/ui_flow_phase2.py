def run(page, base, shots, errors):
    page.click("#nav [data-step=cell]")
    page.get_by_role("link", name="CSV", exact=True).last.click()
    page.wait_for_selector("text=Extraction result")
    page.screenshot(path=f"{shots}/p2_extraction.png", full_page=True)
    page.click("text=Review & confirm the values")
    page.wait_for_selector("text=Confirm cell parameters")
    page.screenshot(path=f"{shots}/p2_confirm.png", full_page=True)
    # accept the cp assumption is only offered on the upload page: go back and accept it
    page.click("#nav [data-step=cell]")
    page.get_by_role("button", name="Accept as assumption").first.click()
    page.click("#nav [data-step=confirm]")
    page.get_by_label("I have reviewed these cell parameters", exact=False).check()
    page.click("#nav [data-step=pack]")
    page.wait_for_selector("text=Topology")
    for label, val in [("Cells in series (Ns)", "120"), ("Cells in parallel (Np)", "1"), ("Number of modules", "10"), ("Cells per module", "12")]:
        page.get_by_label(label, exact=False).first.fill(val)
    page.wait_for_selector("text=Derived pack quantities")
    page.wait_for_timeout(600)
    page.screenshot(path=f"{shots}/p2_pack.png", full_page=True)
    txt = page.inner_text("#view")
    assert "384" in txt and "38.4" in txt, txt[:500]
