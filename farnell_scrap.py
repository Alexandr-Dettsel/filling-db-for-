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

def handle_cookie_banner(driver):
    try:
        btn = driver.find_element(By.ID, "onetrust-reject-all-handler")
        if btn.is_displayed():
            # Нажимаем через JS, чтобы избежать перекрытия другими элементами
            driver.execute_script("arguments[0].click();", btn)
            print("    -> Закрыли баннер с куки (Отклонили)")
    except Exception:
        pass

def check_and_reload_if_403(context, target_url):
    driver = context['driver']
    max_proxy_attempts = len(context['proxies']) + 1 if context['proxies'] else 2
    proxy_attempts = 0
    
    while proxy_attempts < max_proxy_attempts:
        attempts = 0
        while attempts < 3:
            try:
                title = driver.execute_script("return document.title;") or ""
                body_text = driver.execute_script("return document.body ? document.body.innerText.substring(0, 1000) : '';") or ""
            except Exception:
                title = ""
                body_text = ""
                
            if "Access Denied" in title or "403" in title or "Access to this page has been denied" in body_text:
                print(f"    -> [!] Обнаружена ошибка 403 / Access Denied. Ожидание и перезагрузка ({attempts+1}/3)...")
                time.sleep(random.uniform(4, 7))
                try:
                    driver.refresh()
                except:
                    pass
                time.sleep(3)
                attempts += 1
            else:
                return # Успешно загрузили
                
        # Если рефреш не помог - меняем прокси и пересоздаем браузер
        print("    -> [!] Перезагрузка не помогла. Пересоздаем браузер/меняем прокси...")
        try:
            driver.quit()
        except:
            pass
            
        new_proxy = None
        if context['proxies']:
            context['proxy_index'] = (context['proxy_index'] + 1) % len(context['proxies'])
            new_proxy = context['proxies'][context['proxy_index']]
            print(f"    -> Выбран прокси: {new_proxy}")
            
        options = uc.ChromeOptions()
        options.page_load_strategy = 'none'
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        
        # Отключаем лишнее для скорости
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
                # Прокси с авторизацией (host:port:user:pass)
                host, port, user, pwd = parts
                from farnell_scrap import _create_proxy_extension # Вспомогательный хак: функция будет определена ниже
                ext = _create_proxy_extension(host, port, user, pwd)
                options.add_argument(f"--load-extension={ext}")
            elif len(parts) == 2:
                # Прокси без авторизации
                options.add_argument(f'--proxy-server=http://{new_proxy}')
            elif "://" in new_proxy:
                options.add_argument(f'--proxy-server={new_proxy}')
            else:
                options.add_argument(f'--proxy-server=http://{new_proxy}')
                
        #driver = uc.Chrome(options=options, version_main=147)
        driver = uc.Chrome(options=options)
        driver.maximize_window()
        context['driver'] = driver
        
        try:
            driver.get(target_url)
            time.sleep(3)
        except:
            pass
            
        proxy_attempts += 1

def parse_table_page(context, category_name, save_path, base_category_url):
    driver = context['driver']
    print(f"[*] Собираем данные из таблицы для: {category_name}")
    
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
                
    while True:
        current_time = datetime.now().strftime("%H:%M:%S")
        print(f"    -> [{current_time}] Начало сбора данных со страницы {page_num}...")
        
        # Надежное ожидание появления именно строк с товарами
        row_xpath = "//tr[contains(@class, 'ProductListerTablestyles__TableRow')] | //table[@id='s-results']//tr[contains(@class, 'altRow')] | //table[@id='s-results']//tbody/tr"
        
        # Бесконечно ждем, пока товары появятся (мы точно знаем, что страница есть)
        while True:
            try:
                check_script = "return document.evaluate(\"" + row_xpath.replace('"', '\\"') + "\", document, null, 9, null).singleNodeValue !== null;"
                WebDriverWait(driver, 5).until(lambda d: d.execute_script(check_script))
                break
            except TimeoutException:
                check_and_reload_if_403(context, driver.current_url)
                driver = context['driver']
                handle_cookie_banner(driver)
                time.sleep(1)

        # Проверяем и принудительно ставим 50 товаров
        try:
            select_elem = driver.find_element(By.ID, "bx-pagination-select-table-pagination")
            select = Select(select_elem)
            if select.first_selected_option.get_attribute("value") != "50":
                select.select_by_value("50")
                print("    -> Переключили отображение на 50 товаров. Ожидаем обновления данных...")
                # Ждем дольше, так как ajax запрос может быть не быстрым
                time.sleep(6)
        except Exception:
            pass

        # Получаем общее количество страниц (делаем это только на 1-й странице, так как после переключения 50 товаров общее их количество пересчитается)
        if page_num == 1:
            try:
                pagination_xpath = "//span[contains(@class, 'bx--pagination__text')] | //span[@class='paginTotal'] | //div[contains(@class,'pagination')]//span[contains(text(), 'of') or contains(text(), 'из')]"
                page_texts = driver.find_elements(By.XPATH, pagination_xpath)
                
                for pt in page_texts:
                    match = re.search(r'(\d+)', pt.text.replace(' ', ''))
                    if match:
                        found_pages = int(match.group(1))
                        # Если нашли число больше текущего, берем его
                        if found_pages > max_pages:
                            max_pages = found_pages
                            
                print(f"    -> Всего страниц для сбора (по 50 товаров): {max_pages}")
            except Exception as e:
                print(f"    -> Не удалось точно определить количество страниц: {e}")

        # 1. Получаем заголовки таблицы через JS (значительно быстрее)
        header_names = driver.execute_script("""
            let headers = document.evaluate("//thead//th | //th", document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
            let names = [];
            for (let i = 0; i < headers.snapshotLength; i++) {
                let text = headers.snapshotItem(i).innerText;
                if(text && text.trim()) names.push(text.split('\\n')[0].trim());
            }
            return names;
        """)
        
        # 2. Исключаем колонки, которые нам не нужны
        excluded_headers = [
            'Сравнить', 
            'Код заказа', 
            'Производитель / Oписание', 
            'Наличие', 
            'Цена за', 
            'Количество',
            '' # Пустые заголовки
        ]
        
        current_page_products = []
        
        while True:
            try:
                # ИЗВЛЕКАЕМ ДАННЫЕ ЧЕРЕЗ JAVАСCRIPT (Выполняется мгновенно в браузере, а не гоняет запросы через Selenium API)
                js_script = """
                let data = [];
                let rows = document.evaluate(arguments[0], document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                for (let i = 0; i < rows.snapshotLength; i++) {
                    let row = rows.snapshotItem(i);
                    let partNoNode = document.evaluate(".//*[contains(@class, 'PartNumber') or contains(@class, 'mftrPart')]", row, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                    if (!partNoNode) continue;
                    
                    let colsNodes = document.evaluate(".//td[contains(@class, 'extended-attribute') or not(@class)]", row, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
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
                
                # Защита от пустой таблицы во время перерисовки
                if not raw_data:
                    time.sleep(2)
                    continue
                
                current_page_products = []
                
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
                
                # Успешно собрали данные
                break 
                
            except Exception as err:
                print(f"    -> Ошибка при сборе строк таблиц (DOM в процессе обновления). Пробуем еще раз...")
                time.sleep(2)
        
        # Сохранение в файл
        saved_products.extend(current_page_products)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(saved_products, f, ensure_ascii=False, indent=4)
            
        print(f"    -> [Сохранение] Добавлено {len(current_page_products)} товаров (Всего: {len(saved_products)}) в файл")
        
        if page_num >= max_pages:
            print(f"    -> Достигнута последняя страница ({max_pages}). Завершаем сбор категории.")
            break
            
        # 3. Пагинация через URL
        try:
            page_num += 1
            next_url = f"{base_category_url}/prl/results/{page_num}"
            
            # Небольшая задержка перед переходом для имитации человека
            time.sleep(random.uniform(0.5, 2.5))
            
            current_time = datetime.now().strftime("%H:%M:%S")
            print(f"    -> [{current_time}] Переход на страницу {page_num} из {max_pages}: {next_url}")
            
            driver.get(next_url)
            time.sleep(random.uniform(0.5, 2.5))
            
            check_and_reload_if_403(context, next_url)
            driver = context['driver'] # Обновляем драйвер на случай, если он пересоздался с новым прокси
            handle_cookie_banner(driver)
            
        except Exception as e:
            print(f"    -> Ошибка при переходе на следующую страницу: {e}")
            break

def process_url(context, url, current_path, category_name="Root"):
    driver = context['driver']
    clean_cat_name = category_name.replace("/", "_").replace("\\", "_")
    expected_file = os.path.join(current_path, f"{clean_cat_name}.json")
    
    if os.path.exists(expected_file):
        print(f"\n[!] Пропускаем, так как файл уже существует: {expected_file}")
        return

    current_time = datetime.now().strftime("%H:%M:%S")
    print(f"\n[>] [{current_time}] Переход: {url}")
    
    time.sleep(random.uniform(0.5, 2.5))
    driver.get(url)

    check_and_reload_if_403(context, url)
    driver = context['driver'] # Обновляем драйвер на случай, если он пересоздался с новым прокси
    handle_cookie_banner(driver)
    
    print("    -> Ожидание отрисовки контента (до 60 сек)...")
    max_wait = 60
    start_time = time.time()
    table_rows = []
    subcategories = []
    
    # Делаем "умный" цикл ожидания. Если страница тяжелая (SPA),
    # элементы могут появиться в DOM чуть позже, после работы JS-скриптов Farnell
    while time.time() - start_time < max_wait:
        # Проверяем наличие таблицы
        table_rows = driver.find_elements(By.XPATH, "//tr[.//div[contains(@class, 'PartNumber')]] | //tr[contains(@class, 'ProductListerTablestyles__TableRow')] | //table[@id='s-results']//tr[contains(@class, 'altRow')]")
        
        # Надежный поиск подкатегорий - ищем все ссылки <a>, которые ведут на уровень "глубже", чем текущая страница
        current_base = driver.current_url.split("?")[0].rstrip("/")
        all_links = driver.find_elements(By.TAG_NAME, "a")
        
        valid_subcats = []
        # Мы используем dict, чтобы не добавлять дубликаты ссылок с одним и тем же текстом/урлом
        seen_hrefs = set()
        
        for a in all_links:
            try:
                href = a.get_attribute("href")
                # Условие: ссылка должна начинаться с текущего base_url + "/" (значит это вложенная категория)
                if href and href.startswith(current_base + "/") and "prl/results" not in href:
                    if href not in seen_hrefs and a.text.strip():
                        valid_subcats.append(a)
                        seen_hrefs.add(href)
            except Exception:
                continue # Игнорируем StaleElement
                
        subcategories = valid_subcats
        
        # Если нашли хоть что-то из этого - выходим из цикла ожидания
        if table_rows or subcategories:
            break
            
        time.sleep(1)
        # Иногда баннер с куками может всплыть с опозданием и перекрыть клики
        handle_cookie_banner(driver)
    
    if table_rows:
        # ПОДКАТЕГОРИЙ НЕТ, НО ЕСТЬ ТАБЛИЦА -> Значит это конечная таблица товаров
        base_category_url = url.split("?")[0].rstrip("/")
        parse_table_page(context, clean_cat_name, current_path, base_category_url)
        return

    if subcategories:
        # ЭТО СТРАНИЦА КАТЕГОРИЙ -> Создаем папку
        clean_cat_name = category_name.replace("/", "_").replace("\\", "_")
        new_dir = os.path.join(current_path, clean_cat_name)
        os.makedirs(new_dir, exist_ok=True)
        print(f"[+] Создана папка: {new_dir} (Найдено подкатегорий: {len(subcategories)})")
        
        cat_links = []
        for cat in subcategories:
            cat_name = cat.text.strip()
            cat_url = cat.get_attribute('href')
            # Забираем только валидные ссылки
            if cat_url and '/c/' in cat_url and "prl/results" not in cat_url:
                cat_links.append((cat_name, urljoin(BASE_URL, cat_url)))
            
        # Запускаем рекурсию вглубь
        for name, link in cat_links:
            process_url(context, link, new_dir, name)
            
    else:
        print(f"[-] На странице {url} не удалось найти ни список категорий, ни таблицу товаров.")

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

def main():
    print("Введите ссылки на категории Farnell. Для завершения ввода просто нажмите Enter на пустой строке.")
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

    options = uc.ChromeOptions( )
    options.page_load_strategy = 'none'  # Вообще не ждем загрузки (отдает управление мгновенно), ждем только наши элементы через WebDriverWait
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    
    # Отключаем лишнее для скорости
    options.add_argument('--blink-settings=imagesEnabled=false')

    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.stylesheets": 2,
        "profile.managed_default_content_settings.fonts": 2,
        "profile.default_content_setting_values.notifications": 2,
    }
    options.add_experimental_option("prefs", prefs)

    if proxies:
        first_proxy = proxies[0]
        parts = first_proxy.split(":")
        if len(parts) == 4:
            host, port, user, pwd = parts
            ext = _create_proxy_extension(host, port, user, pwd)
            options.add_argument(f"--load-extension={ext}")
        elif len(parts) == 2:
            options.add_argument(f'--proxy-server=http://{first_proxy}')
        elif "://" in first_proxy:
            options.add_argument(f'--proxy-server={first_proxy}')
        else:
            options.add_argument(f'--proxy-server=http://{first_proxy}')
        print(f"[i] Запуск с первым прокси: {first_proxy}")

    #driver = uc.Chrome(options=options, version_main=147)
    driver = uc.Chrome(options=options)
    driver.maximize_window()
    
    context = {
        'driver': driver,
        'proxies': proxies,
        'proxy_index': 0
    }
    
    base_folder = os.path.join(os.path.dirname(__file__), "farnell", "data")
    if not os.path.exists(base_folder):
        os.makedirs(base_folder)
        
    try:
        for start_url in start_urls:
            # Динамически определяем название главной категории из ссылки
            if '/c/' in start_url:
                base_cat_name = start_url.split('/c/')[-1].split('?')[0].replace('/', '_')
            else:
                base_cat_name = "Root_Category"
                
            process_url(context, start_url, base_folder, base_cat_name)
    finally:
        context['driver'].quit()
        print("Скрипт завершил работу.")

if __name__ == "__main__":
    main()
