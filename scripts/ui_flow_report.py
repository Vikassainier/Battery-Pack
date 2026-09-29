def run(page, base, shots, errors):
    """Step 11: generate both reports from the loaded sample project through the real download buttons and verify the files."""
    import io
    import zipfile

    page.get_by_role("button", name="Load sample project").click()
    page.wait_for_selector("text=Readiness")
    page.click("#nav [data-step=report]")
    page.wait_for_selector("text=Generate")
    page.wait_for_timeout(500)
    page.screenshot(path=f"{shots}/r_report_step.png", full_page=True)

    with page.expect_download(timeout=120000) as dl:
        page.get_by_role("button", name="⬇ PDF report").click()
    pdf_path = dl.value.path()
    data = open(pdf_path, "rb").read()
    assert data.startswith(b"%PDF") and len(data) > 200_000, f"PDF too small / invalid: {len(data)} bytes"
    print("PDF download:", dl.value.suggested_filename, len(data) // 1024, "KB")
    page.wait_for_selector("text=Report generated", timeout=10000)

    with page.expect_download(timeout=120000) as dl:
        page.get_by_role("button", name="⬇ Excel workbook").click()
    data = open(dl.value.path(), "rb").read()
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert data[:2] == b"PK" and any(n.startswith("xl/charts/") for n in names), "workbook invalid or without charts"
    print("XLSX download:", dl.value.suggested_filename, len(data) // 1024, "KB,", sum(n.startswith("xl/charts/chart") for n in names), "charts")

    # a blocked analysis (HTTP 422 with a readable detail) must show a message in the page instead of a broken download
    page.route("**/api/report/pdf", lambda route: route.fulfill(status=422, content_type="application/json",
                                                                 body='{"detail": "The analysis is blocked, so no report can be produced. Fix these errors first: Cell data must be confirmed."}'))
    page.get_by_role("button", name="⬇ PDF report").click()
    page.wait_for_selector("text=Cell data must be confirmed", timeout=10000)
    page.screenshot(path=f"{shots}/r_report_blocked.png", full_page=True)
    page.unroute("**/api/report/pdf")
    errors[:] = [e for e in errors if "422" not in e]           # the simulated 422 above is expected, not a defect
