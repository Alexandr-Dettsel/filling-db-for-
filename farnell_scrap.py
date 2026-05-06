import os
import json
import time
import random
from datetime import datetime
from urllib.parse import urljoin
import re
import tempfile

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.common.exceptions import NoSuchElementException, TimeoutException

BASE_URL = "https://ru.farnell.com"

def _create_proxy_extension(host, port, username, password):
    manifest_json = '{"version":"1.0.0","manifest_version":2,"name":"Proxy","permissions":["proxy","tabs","unlimitedStorage","storage","<all_urls>","webRequest","webRequestBlocking"],"background":{"scripts":["background.js"]},"minimum_chrome_version":"22.0.0"}'
    background_js = """
    var config = {mode:"fixed_servers",rules:{singleProxy:{scheme:"http",host:"%s",port:parseInt(%s)},bypassList:["localhost"]}};
    chrome.proxy.settings.set({value:config,scope:"regular"},function(){});
    function callbackFn(details){return{authCredentials:{username:"%s",password:"%s"}};}
    chrome.webRequest.onAuthRequired.addListener(callbackFn,{urls:["<all_urls>"]},['blocking']);
    """ % (host, port, username, password)
    extension_dir = tempfile.mkdtemp()
    with open(os.path.join(extension_dir, "manifest.json"), "w") as f:
        f.write(manifest_json)
    with open(os.path.join(extension_dir, "background.js"), "w") as f:
        f.write(background_js)
    return extension_dir


def handle_cookie_banner(driver):
    try:
        btn = driver.find_element(By.ID, "onetrust-reject-all-handler")
        if btn.is_displayed():
            driver.execute_script("arguments[0].click();", btn)
            print("    -> Закрыли баннер с куки (Отклонили)")
    except Exception:
        pass


def recreate_driver(context, change_proxy=True):
    print("    -> Пересоздаем браузер/меняем прокси...")
    try:
        context['driver'].quit()
    except Exception:
        pass

    new_proxy = None
    if context['proxies'] and change_proxy:
        context['proxy_index'] = (context['proxy_index'] + 1) % len(context['proxies'])
        new_proxy = context['proxies'][context['proxy_index']]
        print(f"    -> Выбран прокси: {new_proxy}")
    elif context['proxies']:
        new_proxy = context['proxies'][context['proxy_index']]

    options = uc.ChromeOptions()
    options.page_load_strategy = 'none'
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--blink-settings=imagesEnabled=false')

    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.stylesheets": 2,
        "profile.managed_default_content_settings.fonts": 2,
        "profile.default_content_setting_values.notifications": 2,
    }
    options.add_experimental_option("prefs", prefs)

    if new_proxy:
        parts = new_proxy.split(":")
        if len(parts) == 4:
            host, port, user, pwd = parts
            ext = _create_proxy_extension(host, port, user, pwd)
            options.add_argument(f"--load-extension={ext}")
        elif len(parts) == 2:
            options.add_argument(f'--proxy-server=http://{new_proxy}')
        elif "://" in new_proxy:
            options.add_argument(f'--proxy-server={new_proxy}')
        else:
            options.add_argument(f'--proxy-server=http://{new_proxy}')

    driver = uc.Chrome(options=options, version_main=147)
    driver.maximize_window()
    context['driver'] = driver


def check_and_reload_if_403(context, target_url):
    driver = context['driver']
    max_proxy_attempts = len(context['proxies']) + 1 if context['proxies'] else 2
    proxy_attempts = 0

    while proxy_attempts < max_proxy_attempts:
        attempts = 0
        while attempts < 3:
            try:
                title = driver.execute_script("return document.title;") or ""
                body_text = driver.execute_script(
                    "return document.body ? document.body.innerText.substring(0, 1000) : '';") or ""
            except Exception:
                title = ""
                body_text = ""

            if "Access Denied" in title or "403" in title or "Access to this page has been denied" in body_text:
                print(f"    -> [!] Обнаружена ошибка 403 / Access Denied. Ожидание и перезагрузка ({attempts + 1}/3)...")
                time.sleep(random.uniform(3, 5))
                try:
                    driver.refresh()
                except Exception:
                    pass
                time.sleep(1)
                attempts += 1
            else:
                return  # Успешно загрузили

        # Перезагрузка не помогла — меняем прокси
        print("    -> [!] Перезагрузка не помогла (403). Меняем прокси...")
        recreate_driver(context, change_proxy=True)
        driver = context['driver']

        try:
            driver.get(target_url)
            time.sleep(1)
        except Exception:
            pass

        proxy_attempts += 1


def load_url(context, url):
    """Загружает URL, при необходимости меняет прокси каждые 5 страниц."""
    context['page_count'] += 1

    # Плановая смена прокси каждые 5 страниц
    if context['page_count'] >= 5:
        print(f"\n    -> [!] Лимит в 5 страниц достигнут. Плановая смена прокси...")
        recreate_driver(context, change_proxy=True)
        context['page_count'] = 1

    driver = context['driver']
    driver.get(url)
    time.sleep(random.uniform(0.5, 1.5))

    check_and_reload_if_403(context, url)

    # После каждой загрузки (в том числе после смены прокси) закрываем куки
    handle_cookie_banner(context['driver'])


def _select_50_per_page(driver):
    """Выбирает 50 товаров на странице, если ещё не выбрано. Возвращает True если переключали."""
    try:
        select_elem = driver.find_element(By.ID, "bx-pagination-select-table-pagination")
        select = Select(select_elem)
        if select.first_selected_option.get_attribute("value") != "50":
            select.select_by_value("50")
            print("    -> Переключили отображение на 50 товаров. Ожидаем обновления...")
            time.sleep(3)
            return True
    except Exception:
        pass
    return False


def _build_page_url(base_category_url, page_num):
    """
    Строит URL страницы.
    base_category_url может уже содержать /prl/results — удаляем его,
    чтобы не получать двойной путь вида /prl/results/prl/results/2.
    """
    # Обрезаем /prl/results и всё после него
    clean_base = re.split(r'/prl/results', base_category_url)[0].rstrip('/')
    return f"{clean_base}/prl/results/{page_num}"


def parse_table_page(context, category_name, save_path, base_category_url):
    driver = context['driver']
    print(f"[*] Собираем данные из таблицы для: {category_name}")

    # Нормализуем базовый URL сразу — убираем /prl/results если есть
    base_category_url = re.split(r'/prl/results', base_category_url)[0].rstrip('/')

    page_num = 1
    max_pages = 1

    file_path = os.path.join(save_path, f"{category_name}.json")
    saved_products = []
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                saved_products = json.load(f)
            except json.JSONDecodeError:
                pass

    row_xpath = (
        "//tr[contains(@class, 'ProductListerTablestyles__TableRow')]"
        " | //table[@id='s-results']//tr[contains(@class, 'altRow')]"
        " | //table[@id='s-results']//tbody/tr"
    )

    while True:
        # Обновляем driver (мог смениться после load_url)
        driver = context['driver']

        current_time = datetime.now().strftime("%H:%M:%S")
        print(
            f"    -> [{current_time}] Страница {page_num}"
            f" (страниц на текущем прокси: {context['page_count']})..."
        )

        # Ждём появления строк товаров
        while True:
            try:
                check_script = (
                    "return document.evaluate(\""
                    + row_xpath.replace('"', '\\"')
                    + "\", document, null, 9, null).singleNodeValue !== null;"
                )
                WebDriverWait(driver, 5).until(lambda d: d.execute_script(check_script))
                break
            except TimeoutException:
                check_and_reload_if_403(context, driver.current_url)
                driver = context['driver']
                handle_cookie_banner(driver)
                time.sleep(1)

        # Подтверждаем режим 50 товаров (особенно важно сразу после смены прокси).
        # Иногда после первой смены select визуально меняется, но нужная страница
        # успевает открыться ещё с 25 товарами.
        for _ in range(3):
            switched = _select_50_per_page(driver)
            if not switched:
                break

            # После переключения сайт сбрасывается на страницу 1 —
            # явно перегружаем нужную страницу по URL, иначе данные съедут.
            correct_url = _build_page_url(base_category_url, page_num)
            print(f"    -> После выбора 50 товаров перегружаем нужную страницу: {correct_url}")
            load_url(context, correct_url)
            driver = context['driver']

            # Ждём строки после перезагрузки.
            while True:
                try:
                    WebDriverWait(driver, 5).until(lambda d: d.execute_script(check_script))
                    break
                except TimeoutException:
                    check_and_reload_if_403(context, correct_url)
                    driver = context['driver']
                    handle_cookie_banner(driver)
                    time.sleep(1)

        # На первой странице определяем общее количество страниц
        if page_num == 1:
            try:
                pagination_xpath = (
                    "//span[contains(@class, 'bx--pagination__text')]"
                    " | //span[@class='paginTotal']"
                    " | //div[contains(@class,'pagination')]//span[contains(text(), 'of') or contains(text(), 'из')]"
                )
                page_texts = driver.find_elements(By.XPATH, pagination_xpath)
                for pt in page_texts:
                    match = re.search(r'(\d+)', pt.text.replace(' ', ''))
                    if match:
                        found_pages = int(match.group(1))
                        if found_pages > max_pages:
                            max_pages = found_pages
                print(f"    -> Всего страниц для сбора (по 50 товаров): {max_pages}")
            except Exception as e:
                print(f"    -> Не удалось определить количество страниц: {e}")

        # Собираем заголовки
        header_names = driver.execute_script("""
            let headers = document.evaluate("//thead//th | //th", document, null,
                XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
            let names = [];
            for (let i = 0; i < headers.snapshotLength; i++) {
                let text = headers.snapshotItem(i).innerText;
                if(text && text.trim()) names.push(text.split('\\n')[0].trim());
            }
            return names;
        """)

        excluded_headers = [
            'Сравнить', 'Код заказа', 'Производитель / Oписание',
            'Наличие', 'Цена за', 'Количество', ''
        ]

        current_page_products = []

        # Собираем строки таблицы (с ретраями при DOM-обновлении)
        while True:
            try:
                js_script = """
                let data = [];
                let rows = document.evaluate(arguments[0], document, null,
                    XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                for (let i = 0; i < rows.snapshotLength; i++) {
                    let row = rows.snapshotItem(i);
                    let partNoNode = document.evaluate(
                        ".//*[contains(@class, 'PartNumber') or contains(@class, 'mftrPart')]",
                        row, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                    if (!partNoNode) continue;

                    let colsNodes = document.evaluate(
                        ".//td[contains(@class, 'extended-attribute') or not(@class)]",
                        row, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                    let attrs = [];
                    for (let j = 0; j < colsNodes.snapshotLength; j++) {
                        let text = colsNodes.snapshotItem(j).innerText;
                        attrs.push(text ? text.trim() : "");
                    }
                    data.push({'part_no': partNoNode.innerText.trim(), 'attrs': attrs});
                }
                return data;
                """
                raw_data = driver.execute_script(js_script, row_xpath)

                if not raw_data:
                    time.sleep(1)
                    continue

                for row_data in raw_data:
                    product_data = {'Номер по каталогу': row_data['part_no']}
                    attr_index = 0
                    for h_name in header_names:
                        if h_name in excluded_headers or h_name == 'Номер по каталогу производителя':
                            continue
                        if attr_index < len(row_data['attrs']):
                            val = row_data['attrs'][attr_index]
                            product_data[h_name] = val if val != "-" else None
                            attr_index += 1
                    current_page_products.append(product_data)

                break

            except Exception:
                print("    -> Ошибка при сборе строк (DOM обновляется). Повтор...")
                time.sleep(1)

        saved_products.extend(current_page_products)

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(saved_products, f, ensure_ascii=False, indent=4)

        print(
            f"    -> [Сохранение] Добавлено {len(current_page_products)} товаров"
            f" (Всего: {len(saved_products)})"
        )

        if page_num >= max_pages:
            print(f"    -> Достигнута последняя страница ({max_pages}). Завершаем категорию.")
            break

        # Переход на следующую страницу
        page_num += 1
        next_url = _build_page_url(base_category_url, page_num)

        current_time = datetime.now().strftime("%H:%M:%S")
        print(f"    -> [{current_time}] Переход на страницу {page_num}/{max_pages}: {next_url}")

        try:
            load_url(context, next_url)
        except Exception as e:
            print(f"    -> Ошибка при переходе на следующую страницу: {e}")
            break


def process_url(context, url, current_path, category_name="Root"):
    driver = context['driver']
    clean_cat_name = category_name.replace("/", "_").replace("\\", "_")
    expected_file = os.path.join(current_path, f"{clean_cat_name}.json")

    if os.path.exists(expected_file):
        print(f"\n[!] Пропускаем, файл уже существует: {expected_file}")
        return

    current_time = datetime.now().strftime("%H:%M:%S")
    print(f"\n[>] [{current_time}] Переход: {url}")

    load_url(context, url)
    driver = context['driver']

    print("    -> Ожидание отрисовки контента (до 60 сек)...")
    max_wait = 60
    start_time = time.time()
    table_rows = []
    subcategories = []

    while time.time() - start_time < max_wait:
        table_rows = driver.find_elements(By.XPATH,
            "//tr[.//div[contains(@class, 'PartNumber')]]"
            " | //tr[contains(@class, 'ProductListerTablestyles__TableRow')]"
            " | //table[@id='s-results']//tr[contains(@class, 'altRow')]"
        )

        current_base = driver.current_url.split("?")[0]
        # Убираем /prl/results/... из current_base для поиска подкатегорий
        current_base_clean = re.split(r'/prl/results', current_base)[0].rstrip('/')

        all_links = driver.find_elements(By.TAG_NAME, "a")
        valid_subcats = []
        seen_hrefs = set()

        for a in all_links:
            try:
                href = a.get_attribute("href")
                if (href
                        and href.startswith(current_base_clean + "/")
                        and "prl/results" not in href):
                    if href not in seen_hrefs and a.text.strip():
                        valid_subcats.append(a)
                        seen_hrefs.add(href)
            except Exception:
                continue

        subcategories = valid_subcats

        if table_rows or subcategories:
            break

        time.sleep(1)
        handle_cookie_banner(driver)

    if table_rows:
        base_category_url = url.split("?")[0].rstrip("/")
        parse_table_page(context, clean_cat_name, current_path, base_category_url)
        return

    if subcategories:
        new_dir = os.path.join(current_path, clean_cat_name)
        os.makedirs(new_dir, exist_ok=True)
        print(f"[+] Создана папка: {new_dir} (Подкатегорий: {len(subcategories)})")

        cat_links = []
        for cat in subcategories:
            cat_name = cat.text.strip()
            cat_url = cat.get_attribute('href')
            if cat_url and '/c/' in cat_url and "prl/results" not in cat_url:
                cat_links.append((cat_name, urljoin(BASE_URL, cat_url)))

        for name, link in cat_links:
            process_url(context, link, new_dir, name)

    else:
        print(f"[-] На странице {url} нет ни категорий, ни таблицы товаров.")


def main():
    print("Введите ссылки на категории Farnell. Пустая строка — завершить ввод.")
    start_urls = []
    while True:
        url = input("Ссылка: ").strip()
        if not url:
            break
        start_urls.append(url)

    if not start_urls:
        print("Ссылки не введены. Завершение работы.")
        return

    proxies = []
    if os.path.exists("proxy.txt"):
        with open("proxy.txt", "r", encoding="utf-8") as f:
            proxies = [line.strip() for line in f if line.strip()]
        if proxies:
            print(f"[i] Найдено {len(proxies)} прокси в proxy.txt")

    context = {
        'driver': None,
        'proxies': proxies,
        'proxy_index': -1,
        'page_count': 0,
    }

    recreate_driver(context, change_proxy=True)

    base_folder = os.path.join(os.path.dirname(__file__), "farnell", "data")
    os.makedirs(base_folder, exist_ok=True)

    try:
        for start_url in start_urls:
            if '/c/' in start_url:
                base_cat_name = start_url.split('/c/')[-1].split('?')[0].replace('/', '_')
                # Убираем prl_results из имени если попало
                base_cat_name = base_cat_name.replace('prl_results_', '').strip('_')
            else:
                base_cat_name = "Root_Category"

            process_url(context, start_url, base_folder, base_cat_name)
    finally:
        if context['driver']:
            context['driver'].quit()
        print("Скрипт завершил работу.")


if __name__ == "__main__":
    main()