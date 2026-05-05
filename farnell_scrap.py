import os
import json
import time
import random
from datetime import datetime
from urllib.parse import urljoin

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.common.exceptions import NoSuchElementException, TimeoutException

BASE_URL = "https://ru.farnell.com"

def human_delay(min_s=1, max_s=3):
    time.sleep(random.uniform(min_s, max_s))

def handle_cookie_banner(driver):
    try:
        btn = driver.find_element(By.ID, "onetrust-reject-all-handler")
        if btn.is_displayed():
            # Нажимаем через JS, чтобы избежать перекрытия другими элементами
            driver.execute_script("arguments[0].click();", btn)
            print("    -> Закрыли баннер с куки (Отклонили)")
            human_delay(1, 2)
    except Exception:
        pass

def parse_table_page(driver, category_name, save_path):
    print(f"[*] Собираем данные из таблицы для: {category_name}")
    
    # Изменяем количество отображаемых элементов на 50 перед началом сбора
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
            human_delay(3, 5) # Ждем, пока перерисуется таблица
    except Exception as e:
        print(f"    -> Не удалось переключить таблицy на 50 элементов (возможно, их мало в этой категории)")

    all_products = []
    
    while True:
        human_delay(3, 5) # Ждем загрузки элементов таблицы
        
        # 1. Получаем заголовки таблицы, чтобы сопоставить их со значениями
        headers = driver.find_elements(By.XPATH, "//thead//th")
        header_names = [h.text.split('\n')[0].strip() for h in headers if h.text.strip()]
        
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
        for attempt in range(max_retries):
            try:
                rows = driver.find_elements(By.XPATH, "//tbody/tr[contains(@class, 'ProductListerTablestyles__TableRow')]")
                current_page_products = []
                
                for row in rows:
                    product_data = {}
                    
                    try:
                        # Номер по каталогу производителя (Part Number)
                        part_no = row.find_element(By.XPATH, ".//div[contains(@class, 'ManufacturerPartNoTableCellstyles__PartNumber')]").text
                        product_data['Номер по каталогу'] = part_no
                    except NoSuchElementException:
                        continue # Если нет парт-номера, пропускаем строку
                        
                    # Собираем остальные динамические характеристики
                    attributes = row.find_elements(By.XPATH, ".//td[contains(@class, 'extended-attribute')]")
                    
                    # Сопоставляем значения со столбцами
                    attr_index = 0
                    for h_name in header_names:
                        if h_name in excluded_headers or h_name == 'Номер по каталогу производителя':
                            continue
                            
                        if attr_index < len(attributes):
                            val = attributes[attr_index].text.strip()
                            product_data[h_name] = val if val != "-" else None
                            attr_index += 1
                            
                    current_page_products.append(product_data)
                
                # Если цикл for завершился без "stale" ошибок, добавляем в общий список и прерываем попытки
                all_products.extend(current_page_products)
                break 
                
            except Exception as err:
                print(f"    -> [Попытка {attempt+1}/{max_retries}] Ошибка при сборе строк таблиц (возможно Stale Element). Пробуем еще раз...")
                human_delay(2, 4)
                if attempt == max_retries - 1:
                    print("    -> Не удалось извлечь данные со страницы после всех попыток.")
            
        # 3. Пагинация
        try:
            # Убедимся, что ничто не перекрывает
            handle_cookie_banner(driver)
            
            next_btn = driver.find_element(By.XPATH, "//button[contains(@class, 'bx--pagination__button--forward')]")
            
            # Проверяем, активна ли кнопка
            if next_btn.get_attribute("disabled") is not None or "disabled" in next_btn.get_attribute("class") or next_btn.get_attribute("disabled") == "true":
                print("    -> Достигнута последняя страница (кнопка отключена).")
                break
                
            # Скроллим до кнопки и кликаем
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_btn)
            human_delay(1, 2)
            
            rows_for_staleness = driver.find_elements(By.XPATH, "//tbody/tr[contains(@class, 'ProductListerTablestyles__TableRow')]")
            if rows_for_staleness:
                first_row = rows_for_staleness[0]
            else:
                first_row = None
            
            driver.execute_script("arguments[0].click();", next_btn)
            current_time = datetime.now().strftime("%H:%M:%S")
            print(f"    -> [{current_time}] Переход на следующую страницу...")
            
            # Ждем обновления таблицы
            if first_row:
                try:
                    WebDriverWait(driver, 20).until(
                        EC.staleness_of(first_row)
                    )
                except TimeoutException:
                    print("    -> [Предупреждение] Таймаут ожидания обновления до новой таблицы (но возможно она и так загрузилась).")
            else:
                human_delay(3, 5)
        except NoSuchElementException:
            print("    -> Кнопка 'Далее' не найдена. Конец категории.")
            break
        except Exception as e:
            print(f"    -> Остановка пагинации (переход в конец). Причина: {e}")
            break

    # 4. Сохраняем собранные данные в JSON
    if all_products:
        file_path = os.path.join(save_path, f"{category_name}.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(all_products, f, ensure_ascii=False, indent=4)
        print(f"[+] Сохранено {len(all_products)} товаров в файл: {file_path}")
    else:
        print(f"[-] Не удалось собрать товары для категории {category_name}")

def process_url(driver, url, current_path, category_name="Root"):
    clean_cat_name = category_name.replace("/", "_").replace("\\", "_")
    expected_file = os.path.join(current_path, f"{clean_cat_name}.json")
    
    if os.path.exists(expected_file):
        print(f"\n[!] Пропускаем, так как файл уже существует: {expected_file}")
        return

    current_time = datetime.now().strftime("%H:%M:%S")
    print(f"\n[>] [{current_time}] Переход: {url}")
    driver.get(url)
    
    # Дадим начальное время на подгрузку страницы
    time.sleep(2)
    handle_cookie_banner(driver)
    
    print("    -> Ожидание отрисовки контента (до 30 сек)...")
    max_wait = 30
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
        parse_table_page(driver, clean_cat_name, current_path)
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
    # Запрашиваем стартовую ссылку у пользователя при запуске
    start_url = input("Введите стартовую ссылку на категорию Farnell: ").strip()
    if not start_url:
        print("Ссылка не введена. Завершение работы.")
        return
        
    # Динамически определяем название главной категории из ссылки (например: passive-components)
    if '/c/' in start_url:
        base_cat_name = start_url.split('/c/')[-1].split('?')[0].replace('/', '_')
    else:
        base_cat_name = "Root_Category"

    options = uc.ChromeOptions()
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')

    # Явно указываем версию драйвера 147, так как ваш браузер версии 147
    driver = uc.Chrome(options=options, version_main=147)
    driver.maximize_window()
    
    base_folder = os.path.join(os.path.dirname(__file__), "farnell", "data")
    if not os.path.exists(base_folder):
        os.makedirs(base_folder)
        
    try:
        process_url(driver, start_url, base_folder, base_cat_name)
    finally:
        driver.quit()
        print("Скрипт завершил работу.")

if __name__ == "__main__":
    main()
