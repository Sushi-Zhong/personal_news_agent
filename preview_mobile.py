from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
    page.goto("http://127.0.0.1:8000/?v=dev")
    page.screenshot(path="mobile-preview.png", full_page=True)
    input("看完后按回车关闭...")
    browser.close()
