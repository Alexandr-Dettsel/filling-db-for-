from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException, TimeoutException
import time
import json
import os
from dataclasses import dataclass
import logging
import argparse
import re
from twocaptcha import TwoCaptcha
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

logging.getLogger('selenium').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('selenium.webdriver.remote.remote_connection').setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BASE_URL = "https://ampero.ru"
API_KEY = "---"


@dataclass
class ElectronicComponent:
    name: str
    tu_number: str
    manufacturer: str
    supplier: str
    source: str = "ampero.ru"
    article: str = ""
    url: str = ""
    category: str = ""
    voltage: str = ""


class AmperoScraper:
    def __init__(self):
        self.components = []
        self.create_directories()
        self.solver = TwoCaptcha(API_KEY)

    def create_directories(self):
        directories = ['data', 'data/results']
        for directory in directories:
            os.makedirs(directory, exist_ok=True)

    def setup_driver(self):
        options = Options()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-extensions")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--log-level=3")
        options.add_experimental_option('excludeSwitches', ['enable-logging'])
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)

        driver = webdriver.Edge(options=options)
        driver.maximize_window()
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return driver

    def human_like_actions(self, driver):
        try:
            scroll_height = driver.execute_script("return document.body.scrollHeight")
            if scroll_height > 500:
                driver.execute_script("window.scrollTo(0, 300);")
                time.sleep(0.5)
                driver.execute_script("window.scrollTo(0, 200);")
        except:
            pass

    def is_captcha_page(self, driver):
        try:
            captcha_indicators = [
                "captcha",
                "recaptcha",
                "подтвердите, что вы не робот",
                "проверка безопасности",
                "security check",
                "cloudflare"
            ]

            page_text = driver.page_source.lower()

            if len(page_text) < 10000:
                for indicator in captcha_indicators:
                    if indicator in page_text:
                        return True

            captcha_selectors = [
                "iframe[src*='recaptcha']",
                "iframe[data-testid='checkbox-iframe']",
                "iframe[data-testid='advanced-iframe']",
                "div[class*='captcha']",
                "div.g-recaptcha",
                "#challenge-form"
            ]

            for selector in captcha_selectors:
                if driver.find_elements(By.CSS_SELECTOR, selector):
                    return True

            return False
        except:
            return False

    def solver_simple_captcha(self, driver):
        try:
            iframe = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "iframe[data-testid='checkbox-iframe']"))
            )
            driver.switch_to.frame(iframe)
            time.sleep(1)
            checkbox = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "js-button"))
            )
            driver.execute_script("""
                var event = new MouseEvent('click', {
                    view: window,
                    bubbles: true,
                    cancelable: true
                });
                arguments[0].dispatchEvent(event);
            """, checkbox)
            driver.switch_to.default_content()
            logger.info('checkbox отмечен')
            return True
        except Exception as e:
            logger.warning(f"Ошибка решения простой капчи: {e}")
            driver.switch_to.default_content()
            return False

    def get_captcha_images(self, driver):
        try:
            iframe = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "iframe[data-testid='advanced-iframe']"))
            )
            driver.switch_to.frame(iframe)

            canvas_element = driver.find_element(By.CSS_SELECTOR, "div.AdvancedCaptcha-CanvasContainer canvas")
            instruction_image = canvas_element.screenshot_as_base64

            img_element = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.AdvancedCaptcha-ImageWrapper img"))
            )
            main_image = img_element.screenshot_as_base64

            driver.switch_to.default_content()

            logger.info("Получены изображения капчи")
            return main_image, instruction_image, img_element

        except Exception as e:
            logger.warning(f"Ошибка получения изображений: {e}")
            driver.switch_to.default_content()
            return None, None, None

    def solver_difficult_captcha(self, driver):
        try:
            main_img, instruction_img, img_element = self.get_captcha_images(driver)

            if not main_img or not instruction_img:
                logger.warning("Не получили изображения")
                return False

            logger.info("Отправка в 2captcha")

            result = self.solver.coordinates(
                file=f"data:image/png;base64,{main_img}",
                hintImg=f"data:image/png;base64,{instruction_img}"
            )

            coordinates = self.parse_coordinates(result['code'])
            self.click_silhouettes(driver, coordinates)

            time.sleep(1)

            iframe = driver.find_element(By.CSS_SELECTOR, "iframe[data-testid='advanced-iframe']")
            driver.switch_to.frame(iframe)

            submit_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-testid='submit']"))
            )
            submit_btn.click()
            logger.info("Submit нажат")

            driver.switch_to.default_content()
            time.sleep(1)
            return True
        except Exception as e:
            logger.error(f"Ошибка решения сложной капчи: {e}")
            driver.switch_to.default_content()
            return False

    def parse_coordinates(self, coords_string):
        coords_string = coords_string.replace("coordinates:", "").strip()
        pairs = coords_string.split(";")

        coordinates = []
        for pair in pairs:
            if "," not in pair:
                continue
            parts = pair.split(",")
            x = int(parts[0].split("=")[1])
            y = int(parts[1].split("=")[1])
            coordinates.append({"x": x, "y": y})

        return coordinates

    def click_silhouettes(self, driver, coordinates):
        try:
            iframe = driver.find_element(By.CSS_SELECTOR, "iframe[data-testid='advanced-iframe']")
            driver.switch_to.frame(iframe)

            img_element = driver.find_element(By.CSS_SELECTOR, "div.AdvancedCaptcha-ImageWrapper img")

            img_width = img_element.size['width']
            img_height = img_element.size['height']

            original_width = driver.execute_script("return arguments[0].naturalWidth;", img_element)
            original_height = driver.execute_script("return arguments[0].naturalHeight;", img_element)

            scale_x = img_width / original_width
            scale_y = img_height / original_height
            action = ActionChains(driver)

            for i, coord in enumerate(coordinates, 1):
                scaled_x = int(coord['x'] * scale_x)
                scaled_y = int(coord['y'] * scale_y)

                offset_x = scaled_x - (img_width // 2)
                offset_y = scaled_y - (img_height // 2)

                action.move_to_element_with_offset(
                    img_element,
                    offset_x,
                    offset_y
                ).click().perform()
                time.sleep(0.8)

            driver.switch_to.default_content()
            logger.info("✓ Все клики выполнены")
            return True

        except Exception as e:
            logger.error(f"Ошибка кликов: {e}")
            driver.switch_to.default_content()
            return False

    def handle_captcha(self, driver, url):
        logger.info("Обнаружена капча, начинаем автоматическое решение...")

        max_attempts = 3
        for attempt in range(max_attempts):
            logger.info(f"Попытка решения капчи {attempt + 1}/{max_attempts}")

            if self.solver_simple_captcha(driver):
                time.sleep(3)
                if not self.is_captcha_page(driver):
                    logger.info("Простая капча успешно решена")
                    return True

            if self.solver_difficult_captcha(driver):
                time.sleep(3)
                if not self.is_captcha_page(driver):
                    logger.info("Сложная капча успешно решена")
                    return True

            logger.warning(f"Попытка {attempt + 1} не удалась")
            time.sleep(2)

        logger.error("Автоматическое решение капчи не удалось")
        print("\n===-===- КАПЧА ОБНАРУЖЕНА =-==-===")
        print("1. Решите капчу в открывшемся браузере")
        print("2. После успешного решения нажмите Enter здесь")
        input("После решения капчи нажмите Enter для продолжения...")

        driver.get(url)
        time.sleep(3)
        return True

    def get_category_from_user(self):
        print("\nВвод URL категории")
        while True:
            url = input("Введите URL категории Ampero: ").strip()
            if 'ampero.ru' in url:
                category_name = self.extract_category_name_from_url(url)
                return url, category_name
            else:
                print(f"URL должен быть с домена {BASE_URL}")

    def extract_category_name_from_url(self, url):
        try:
            path = urlparse(url).path
            segments = [s for s in path.split('/') if s]
            if segments:
                name = segments[-1]
                name = name.replace('-', ' ').title()
                return name
        except:
            pass
        return "Custom_Category"

    def get_page_range_from_user(self):
        print("\nДиапазон страниц")
        while True:
            try:
                start_page = int(input("С какой страницы начать парсинг? (1): ") or "1")
                end_page = int(input("По какую страницу парсить?: "))
                if 1 <= start_page <= end_page:
                    return start_page, end_page
                else:
                    print("Ошибка: начальная страница должна быть не меньше 1 и не больше конечной")
            except ValueError:
                print("Ошибка: введите целые числа")

    def construct_category_url(self, base_url, page_num):
        parsed = urlparse(base_url)
        query = parse_qs(parsed.query)

        query['limit'] = ['100']

        if page_num > 1:
            query['page'] = [str(page_num)]
        elif 'page' in query:
            del query['page']

        new_query = urlencode(query, doseq=True)
        new_url = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        ))
        return new_url

    def get_product_features_with_retry(self, driver, url, category, max_retries=3):
        for attempt in range(max_retries):
            try:
                driver.get(url)

                if self.is_captcha_page(driver):
                    self.handle_captcha(driver, url)
                    continue

                WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'h1')))
                self.human_like_actions(driver)

                try:
                    product_name = driver.find_element(By.TAG_NAME, 'h1').text.strip()
                except:
                    product_name = "Unknown"

                logger.info(f"Товар: {product_name}")

                article = ""
                try:
                    sku_elem = driver.find_element(By.CSS_SELECTOR, ".product-info__article.sku span")
                    article = sku_elem.text.strip()
                except:
                    pass

                voltage = ""
                manufacturer = "Не определен"

                try:
                    params = driver.find_elements(By.CSS_SELECTOR, ".product__params .product__param")
                    for param in params:
                        try:
                            name_div = param.find_element(By.CSS_SELECTOR, ".product__param-name")
                            val_div = param.find_element(By.CSS_SELECTOR, ".product__param-value")

                            p_name = name_div.text.strip().lower()
                            p_val = val_div.text.strip()

                            if "напряжение" in p_name and "рабочее" in p_name:
                                voltage = p_val
                            elif "производитель" in p_name:
                                manufacturer = p_val
                        except:
                            continue
                except:
                    pass

                if manufacturer == "Не определен":
                    try:
                        rows = driver.find_elements(By.CSS_SELECTOR, ".stock-container .row")
                        for row in rows:
                            text = row.text
                            if "Производитель" in text:
                                cols = row.find_elements(By.CSS_SELECTOR, ".col-xs-8")
                                if cols:
                                    manufacturer = cols[0].text.strip()
                    except:
                        pass

                component = ElectronicComponent(
                    name=product_name,
                    tu_number=article,
                    manufacturer=manufacturer,
                    supplier="Ampero",
                    article=article,
                    url=url,
                    category=category,
                    voltage=voltage
                )

                return component

            except TimeoutException:
                if self.is_captcha_page(driver):
                    logger.warning(f"Таймаут из-за капчи (попытка {attempt + 1})")
                    self.handle_captcha(driver, url)
                else:
                    logger.error(f"Таймаут при загрузке {url}")
                    if attempt == max_retries - 1:
                        return None

            except Exception as e:
                logger.error(f"Ошибка парсинга товара {url}: {e}")
                if attempt == max_retries - 1:
                    return None

        return None

    def parse_category_page_range(self, driver, category_url, category_name, start_page, end_page):
        logger.info(f"Парсинг категории: {category_name}, страницы {start_page}-{end_page}")

        category_components = []

        for page_num in range(start_page, end_page + 1):

            page_url = self.construct_category_url(category_url, page_num)
            logger.info(f"Страница {page_num} - {page_url}")

            try:
                driver.get(page_url)

                if self.is_captcha_page(driver):
                    logger.warning(f"Капча на странице категории {page_num}")
                    self.handle_captcha(driver, page_url)

                self.human_like_actions(driver)

                product_link_selector = ".product-info__name.name a"

                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, product_link_selector)))
                except TimeoutException:
                    logger.info(f"На странице {page_num} товары не найдены (возможно, конец списка)")
                    break

                product_elements = driver.find_elements(By.CSS_SELECTOR, product_link_selector)
                product_urls = [el.get_attribute('href') for el in product_elements]

                product_urls = list(set(filter(None, product_urls)))

                if not product_urls:
                    logger.info(f"На странице {page_num} нет ссылок на товары")
                    break

                logger.info(f"Найдено {len(product_urls)} товаров на странице {page_num}")

                for i, product_url in enumerate(product_urls):
                    logger.info(f"Парсинг товара {i + 1}/{len(product_urls)}")
                    component = self.get_product_features_with_retry(driver, product_url, category_name)
                    if component:
                        category_components.append(component)

            except Exception as e:
                logger.error(f"Ошибка обработки страницы {page_num}: {e}")
                continue

        return category_components

    def save_results(self, components, filename):
        if not components:
            logger.warning("Нет данных для сохранения")
            return

        filepath = os.path.join('data', 'results', filename)

        data = []
        for comp in components:
            data.append({
                'name': comp.name,
                'tu_number': comp.tu_number,
                'manufacturer': comp.manufacturer,
                'supplier': comp.supplier,
                'source': comp.source,
                'article': comp.article,
                'url': comp.url,
                'category': comp.category,
                'voltage': comp.voltage
            })

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"Результаты сохранены в {filepath} ({len(data)} записей)")
        except Exception as e:
            logger.error(f"Ошибка сохранения в {filepath}: {e}")

    def run(self):
        logger.info("Запуск парсера Ampero")
        start_time = time.time()

        driver = self.setup_driver()

        try:
            category_url, category_name = self.get_category_from_user()
            if not category_url:
                logger.error("Категория не выбрана")
                return

            logger.info(f"Выбрана категория: {category_name}")
            start_page, end_page = self.get_page_range_from_user()

            if start_page is None or end_page is None:
                return

            components = self.parse_category_page_range(driver, category_url, category_name, start_page, end_page)

            safe_category_name = "".join(c for c in category_name if c.isalnum() or c in (' ', '_', '-')).rstrip()
            safe_category_name = safe_category_name.replace(' ', '_')
            filename = f"ampero_{safe_category_name}_{start_page}_to_{end_page}.json"

            self.save_results(components, filename)

            total_time = time.time() - start_time
            logger.info(f"Парсинг завершен за {total_time:.1f} секунд")

        finally:
            driver.quit()


if __name__ == "__main__":
    scraper = AmperoScraper()
    scraper.run()