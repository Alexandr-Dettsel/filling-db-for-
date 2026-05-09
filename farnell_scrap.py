# python
import os
import json
import time
import random
from datetime import datetime
from urllib.parse import urljoin
import re

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

def extract_start_page_from_url(url):
    m = re.search(r'/prl/results/(\d+)', url)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return 1
    return 1

def smooth_scroll(driver, duration_seconds=1.0):
    """
    Плавно прокручивает страницу вниз за указанное время (ms). Использует execute_async_script чтобы дождаться конца.
    """
    try:
        ms = int(max(100, duration_seconds * 1000))
        driver.execute_async_script("""
        const done = arguments[arguments.length-1];
        const duration = arguments[0];
        const start = performance.now();
        const initial = window.scrollY || window.pageYOffset;
        const maxY = Math.max(document.body.scrollHeight - window.innerHeight, 0);
        function step(now){
            const t = Math.min((now - start) / duration, 1);
            // ease (smooth)
            const ease = t < 0.5 ? 2*t*t : -1 + (4 - 2*t)*t;
            const y = initial + (maxY - initial) * ease * 0.6; // скроллим не до конца (0.6) чтобы не скакать слишком сильно
            window.scrollTo(0, Math.round(y));
            if (t < 1) requestAnimationFrame(step);
            else setTimeout(done, 50);
        }
        requestAnimationFrame(step);
        """, ms)
    except Exception:
        pass

def move_mouse_random(driver, steps=6):
    """
    Имитирует движение мышки внутри окна, диспатчая события mousemove.
    """
    try:
        driver.execute_script("""
        const steps = arguments[0] || 6;
        for (let i = 0; i < steps; i++) {
            const x = Math.floor(Math.random()*window.innerWidth);
            const y = Math.floor(Math.random()*window.innerHeight);
            const ev = new MouseEvent('mousemove', {clientX: x, clientY: y, bubbles: true});
            document.dispatchEvent(ev);
        }
        """, steps)
    except Exception:
        pass

def parse_table_page(driver, category_name, save_path, base_category_url, start_page=1):
    print(f"[*] Собираем данные из таблицы для: {category_name}")

    all_products = []
    page_num = start_page
    max_pages = 1

    while True:
        current_time = datetime.now().strftime("%H:%M:%S")
        print(f"    -> [{current_time}] Начало сбора данных со страницы {page_num}...")

        # перед сбором имитируем активность пользователя
        move_mouse_random(driver, steps=6)
        smooth_scroll(driver, duration_seconds=0.9)
        time.sleep(0.5)

        # Изменяем количество отображаемых элементов на 50 перед началом сбора только на стартовой странице
        if page_num == start_page:
            try:
                # Ищем селект по ID
                select_elem = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.ID, "bx-pagination-select-table-pagination"))
                )
                select = Select(select_elem)

                # Если сейчас выбрано не 50, переключаем
                if select.first_selected_option.get_attribute("value") != "50":
                    select.select_by_value("50")
                    print("    -> Переключили отображение на 50 товаров на страницу")
                    time.sleep(2) # Небольшая задержка для загрузки и пересчета страниц

                # Получаем общее количество страниц
                pagination_xpath = "//span[contains(@class, 'bx--pagination__text')] | //span[@class='paginTotal'] | //div[contains(@class,'pagination')]//span[contains(text(), 'of') or contains(text(), 'из')]"
                page_texts = driver.find_elements(By.XPATH, pagination_xpath)

                for pt in page_texts:
                    match = re.search(r'(\d+)', pt.text.replace(' ', ''))
                    if match:
                        found_pages = int(match.group(1))
                        if found_pages > max_pages:
                            max_pages = found_pages

                print(f"    -> Всего страниц для сбора: {max_pages}")
            except Exception as e:
                print(f"    -> Не удалось точно определить количество страниц. Ошибка: {e}")

        # Надежное ожидание появления именно строк с товарами
        row_xpath = "//tr[contains(@class, 'ProductListerTablestyles__TableRow')] | //table[@id='s-results']//tr[contains(@class, 'altRow')] | //table[@id='s-results']//tbody/tr"
        try:
            WebDriverWait(driver, 10).until(
                lambda d: len(d.find_elements(By.XPATH, row_xpath)) > 0
            )
        except Exception:
            pass

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

        # Обрабатываем строки с перехватом возможных StaleElementReferenceException
        max_retries = 3
        current_page_products = []

        for attempt in range(max_retries):
            try:
                # ИЗВЛЕКАЕМ ДАННЫЕ ЧЕРЕЗ JAVASCRIPT (Выполняется мгновенно в браузере, а не гоняет запросы через Selenium API)
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

                # Если таблица пуста, возможно страница просто очень долго грузится - дадим ей еще шанс
                if not raw_data and attempt < max_retries - 1:
                    print(f"    -> [Ожидание] Товары пока не появились на странице (Попытка {attempt+1}/{max_retries}). Ждем еще 2 сек...")
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

                # Если удалось обработать все строки (даже если их 0 в конце), выходим из цикла попыток
                break

            except Exception as err:
                print(f"    -> [Попытка {attempt+1}/{max_retries}] Ошибка при сборе строк таблиц (возможно Stale Element). Пробуем еще раз...")
                time.sleep(2)
                if attempt == max_retries - 1:
                    print("    -> Не удалось извлечь данные со страницы после всех попыток.")

        if not current_page_products:
            print("    -> Товаров на странице не найдено. Убедились окончательно. Похоже, это конец категории (или нет результатов).")
            return

        # Сохранение в файл
        file_path = os.path.join(save_path, f"{category_name}.json")

        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                try:
                    saved_products = json.load(f)
                except json.JSONDecodeError:
                    saved_products = []
        else:
            saved_products = []

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

            current_time = datetime.now().strftime("%H:%M:%S")
            print(f"    -> [{current_time}] Переход на страницу {page_num} из {max_pages}: {next_url}")

            driver.get(next_url)
            time.sleep(1)
            handle_cookie_banner(driver)
            # имитация поведения пользователя на новой странице
            move_mouse_random(driver, steps=5)
            smooth_scroll(driver, duration_seconds=0.8)
            time.sleep(0.8)

        except Exception as e:
            print(f"    -> Ошибка при переходе на следующую страницу: {e}")
            break

def process_url(driver, url, current_path, category_name="Root"):
    clean_cat_name = category_name.replace("/", "_").replace("\\", "_")
    expected_file = os.path.join(current_path, f"{clean_cat_name}.json")

    if os.path.exists(expected_file):
        print(f"\n[!] Пропускаем, так как файл уже существует: {expected_file}")
        return

    current_time = datetime.now().strftime("%H:%M:%S")
    print(f"\n[>] [{current_time}] Переход: {url}")

    # Определяем стартовую страницу, если она указана в URL
    start_page = extract_start_page_from_url(url)
    # Базовый url категории без /prl/results/<n>
    base_category_url = url.split("/prl/results/")[0].split("?")[0].rstrip("/")

    driver.get(url)

    time.sleep(1.5)
    handle_cookie_banner(driver)

    # Немного симулируем активность пользователя сразу после захода
    move_mouse_random(driver, steps=8)
    smooth_scroll(driver, duration_seconds=1.2)

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
        parse_table_page(driver, clean_cat_name, current_path, base_category_url, start_page=start_page)
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
            process_url(driver, link, new_dir, name)

    else:
        print(f"[-] На странице {url} не удалось найти ни список категорий, ни таблицу товаров.")

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

    options = uc.ChromeOptions( )
    options.page_load_strategy = 'none'  # Вообще не ждем загрузки (отдает управление мгновенно), ждем только наши элементы через WebDriverWait
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--no-sandbox')
    options.add_argument('--blink-settings=imagesEnabled=false')
    options.add_argument('--disable-dev-shm-usage')

    #driver = uc.Chrome(options=options, version_main=147)
    driver = uc.Chrome(options=options)
    driver.maximize_window()

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

            process_url(driver, start_url, base_folder, base_cat_name)
    finally:
        driver.quit()
        print("Скрипт завершил работу.")

if __name__ == "__main__":
    main()