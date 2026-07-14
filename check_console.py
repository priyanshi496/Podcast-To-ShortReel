from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    def handle_console(msg):
        print(f"[{msg.type}] {msg.text}")
        
    def handle_page_error(err):
        print(f"[page-error] {err}")
        
    page.on("console", handle_console)
    page.on("pageerror", handle_page_error)
    
    print("Navigating to http://localhost:8000/...")
    try:
        page.goto("http://localhost:8000/", wait_until="networkidle")
        print("Page title:", page.title())
        print("Body content length:", len(page.content()))
    except Exception as e:
        print("Navigation failed:", e)
        
    browser.close()
