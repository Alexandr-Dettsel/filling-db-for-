import base64
import struct
from time import sleep

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
import tempfile
from dataclasses import dataclass
import logging
import re
import itertools
from twocaptcha import TwoCaptcha
from dotenv import load_dotenv

load_dotenv()

logging.getLogger('selenium').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('selenium.webdriver.remote.remote_connection').setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.chipdip.ru"
API_KEY = os.getenv("TWOCAPTCHA_API_KEY", "---")


def _parse_proxies():
    raw = os.getenv("PROXIES", "")
    proxies = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) == 4:
            proxies.append({"host": parts[0], "port": parts[1], "user": parts[2], "pass": parts[3]})
    return proxies


PROXY_LIST = _parse_proxies()
_proxy_cycle = itertools.cycle(PROXY_LIST) if PROXY_LIST else None


@dataclass
class ElectronicComponent:
    name: str
    tu_number: str
    manufacturer: str
    supplier: str
    source: str = "chipdip.ru"
    article: str = ""
    url: str = ""
    category: str = ""
    voltage: str = ""


class ChipDipScraper:
    def __init__(self):
        self.components = []
        self.create_directories()
        self.solver = TwoCaptcha(API_KEY)
        self._rotate_every = 1  # менять прокси каждые N страниц категории

    def create_directories(self):
        directories = ['data', 'data/results']
        for directory in directories:
            os.makedirs(directory, exist_ok=True)

    def _create_proxy_extension(self, host, port, username, password):
        """Создает расширение Chrome/Edge для прокси с аутентификацией"""
        manifest_json = """
        {
            "version": "1.0.0",
            "manifest_version": 2,
            "name": "Chrome Proxy",
            "permissions": [
                "proxy",
                "tabs",
                "unlimitedStorage",
                "storage",
                "<all_urls>",
                "webRequest",
                "webRequestBlocking"
            ],
            "background": {
                "scripts": ["background.js"]
            },
            "minimum_chrome_version":"22.0.0"
        }
        """
        
        background_js = """
        var config = {
            mode: "fixed_servers",
            rules: {
                singleProxy: {
                    scheme: "http",
                    host: "%s",
                    port: parseInt(%s)
                },
                bypassList: ["localhost"]
            }
        };
        
        chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});
        
        function callbackFn(details) {
            return {
                authCredentials: {
                    username: "%s",
                    password: "%s"
                }
            };
        }
        
        chrome.webRequest.onAuthRequired.addListener(
            callbackFn,
            {urls: ["<all_urls>"]},
            ['blocking']
        );
        """ % (host, port, username, password)
        
        # Создаем временную директорию для расширения
        extension_dir = tempfile.mkdtemp()
        
        # Создаем файлы расширения
        with open(os.path.join(extension_dir, "manifest.json"), "w") as f:
            f.write(manifest_json)
        
        with open(os.path.join(extension_dir, "background.js"), "w") as f:
            f.write(background_js)
        
        return extension_dir

    def setup_driver(self):
        options = Options()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--log-level=3")
        options.add_argument("--disable-logging")
        options.add_experimental_option('excludeSwitches', ['enable-logging'])
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        if PROXY_LIST:
            p = next(_proxy_cycle)
            proxy_extension = self._create_proxy_extension(p["host"], p["port"], p["user"], p["pass"])
            options.add_argument(f"--load-extension={proxy_extension}")
            logger.info(f"Прокси: {p['host']}:{p['port']}")
        else:
            logger.warning("Прокси не настроены")

        driver = webdriver.Edge(options=options)
        driver.maximize_window()
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return driver

    def human_like_actions(self, driver):
        try:
            scroll_height = driver.execute_script("return document.body.scrollHeight")
            if scroll_height > 500:
                driver.execute_script("window.scrollTo(0, 300);")
                driver.execute_script("window.scrollTo(0, 200);")
        except:
            pass

    def is_captcha_page(self, driver):
        try:
            captcha_indicators = [
                "smartcaptcha",
                "подтвердите, что вы не робот",
                "проверка безопасности",
                "я не робот",
            ]

            page_text = driver.page_source.lower()
            for indicator in captcha_indicators:
                if indicator in page_text:
                    return True

            captcha_selectors = [
                "div#checkbox",  # Простая капча Яндекса
                "div#advanced",  # Сложная капча Яндекса
                "iframe[src*='recaptcha']",
                "iframe[data-testid='checkbox-iframe']",
                "iframe[data-testid='advanced-iframe']",
                "div[class*='captcha']",
                "div.g-recaptcha",
                "div.recaptcha",
                "div.CheckboxCaptcha",
                "div.AdvancedCaptcha",
                "div.Modal_visible",  # Модальное окно капчи
            ]

            for selector in captcha_selectors:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    logger.debug(f"Найдена капча по селектору: {selector}")
                    return True

            return False
        except:
            return False

    def solver_simple_captcha(self, driver):
        try:
            time.sleep(2)

            checkbox = None
            try:
                checkbox = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.ID, "js-button"))
                )
                logger.info("Найдена кнопка по ID js-button")
            except TimeoutException:
                try:
                    checkbox = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "input.CheckboxCaptcha-Button"))
                    )
                    logger.info("Найдена кнопка по CSS селектору")
                except TimeoutException:
                    logger.warning("Кнопка капчи не найдена")
                    return False

            # Сначала пробуем ActionChains (человекоподобный клик)
            try:
                ActionChains(driver).move_to_element(checkbox).click().perform()
            except Exception:
                # Fallback на JS клик
                driver.execute_script("arguments[0].click();", checkbox)

            time.sleep(3)
            logger.info('Простая капча — клик выполнен')
            return True
        except Exception as e:
            logger.warning(f"Ошибка решения простой капчи: {e}")
            return False

    def get_sitekey_from_iframe(self, driver):
        try:
            iframe = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "iframe[data-testid='advanced-iframe']"))
            )
            iframe_src = iframe.get_attribute('src')

            if 'sitekey=' in iframe_src:
                sitekey = iframe_src.split('sitekey=')[1].split('&')[0]
                logger.info(f"Найден sitekey: {sitekey}")
                return sitekey
            else:
                logger.warning("Нет sitekey в iframe")
                return None

        except Exception as e:
            logger.warning(f"Ошибка получения sitekey: {e}")
            return None

    def get_captcha_images(self, driver):
        try:
            # Новая структура - изображения находятся в модальном окне, не в iframe
            time.sleep(2)  # Даем время на загрузку React компонентов

            # Ищем контейнер с изображением
            main_img_element = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "img[alt='Задание с картинкой']"))
            )
            main_image = main_img_element.screenshot_as_base64

            # Ищем изображение с инструкциями (может быть second image)
            try:
                instruction_img_element = driver.find_element(By.CSS_SELECTOR, "img.TaskImage")
                instruction_image = instruction_img_element.screenshot_as_base64
            except:
                # Если нет отдельного изображения инструкции, используем основное
                instruction_image = main_image

            driver.switch_to.default_content()
            logger.info("Получены изображения капчи")
            return main_image, instruction_image, main_img_element

        except Exception as e:
            logger.warning(f"Ошибка получения изображений: {e}")
            driver.switch_to.default_content()
            return None, None, None

    def solver_difficult_captcha(self, driver):
        try:
            time.sleep(2)  # Даем время на загрузку модального окна
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
            self.click_silhouettes(driver, coordinates, is_modal=True)

            time.sleep(1)

            # Ищем кнопку отправки в новой структуре (modal)
            try:
                submit_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-testid='submit']"))
                )
                submit_btn.click()
                logger.info("Submit нажат")
            except TimeoutException:
                logger.warning("Кнопка submit не найдена, пытаюсь альтернативный способ")
                driver.execute_script("""
                    var btn = document.querySelector('button[data-testid="submit"]');
                    if (btn) btn.click();
                """)

            time.sleep(1)
            return True
        except Exception as e:
            logger.error(f"Ошибка решения сложной капчи: {e}")
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

    def click_silhouettes(self, driver, coordinates, is_modal=False):
        try:
            if is_modal:
                img_element = driver.find_element(By.CSS_SELECTOR, "div.AdvancedCaptcha-ImageWrapper img")
            else:
                iframe = driver.find_element(By.CSS_SELECTOR, "iframe[data-testid='advanced-iframe']")
                driver.switch_to.frame(iframe)
                img_element = driver.find_element(By.CSS_SELECTOR, "div.AdvancedCaptcha-ImageWrapper img")

            # Логические CSS пиксели (в них работает move_to_element_with_offset)
            sel_w = img_element.size['width']
            sel_h = img_element.size['height']

            # screenshot_as_base64 делает скриншот в физических пикселях (sel * dpr).
            # 2captcha получила картинку этого размера и дала координаты для неё.
            # Читаем реальный размер PNG из заголовка IHDR и масштабируем координаты обратно.
            png_bytes = base64.b64decode(img_element.screenshot_as_base64)
            screenshot_w = struct.unpack('>I', png_bytes[16:20])[0]
            screenshot_h = struct.unpack('>I', png_bytes[20:24])[0]

            coord_scale_x = sel_w / screenshot_w if screenshot_w else 1
            coord_scale_y = sel_h / screenshot_h if screenshot_h else 1

            logger.info(f"Img CSS={sel_w}x{sel_h}  screenshot={screenshot_w}x{screenshot_h}  scale={coord_scale_x:.3f}x{coord_scale_y:.3f}")

            for i, coord in enumerate(coordinates, 1):
                click_x = coord['x'] * coord_scale_x
                click_y = coord['y'] * coord_scale_y
                offset_x = int(click_x - sel_w / 2)
                offset_y = int(click_y - sel_h / 2)

                logger.info(f"Клик {i}: orig=({coord['x']},{coord['y']}) -> offset=({offset_x},{offset_y})")

                ActionChains(driver)\
                    .move_to_element_with_offset(img_element, offset_x, offset_y)\
                    .click()\
                    .perform()
                time.sleep(0.8)

            if not is_modal:
                driver.switch_to.default_content()
            logger.info("✓ Все клики выполнены")
            return True

        except Exception as e:
            logger.error(f"Ошибка кликов: {e}")
            if not is_modal:
                driver.switch_to.default_content()
            return False

    def get_captcha_type(self, driver):
        """Определяет тип капчи: 'simple', 'difficult' или None"""
        try:
            page_source = driver.page_source
            # Сложная капча — модальное окно с картинками
            if 'AdvancedCaptcha' in page_source or 'div id="advanced"' in page_source or 'id="advanced"' in page_source:
                return 'difficult'
            # Простая капча — чекбокс
            if 'CheckboxCaptcha' in page_source or 'div id="checkbox"' in page_source or 'id="checkbox"' in page_source:
                return 'simple'
        except:
            pass
        return None

    def handle_captcha(self, driver, url):
        logger.info("Обнаружена капча, начинаем автоматическое решение...")

        max_attempts = 3
        for attempt in range(max_attempts):
            logger.info(f"Попытка решения капчи {attempt + 1}/{max_attempts}")

            captcha_type = self.get_captcha_type(driver)
            logger.info(f"Тип капчи: {captcha_type}")

            solved = False
            if captcha_type == 'simple':
                solved = self.solver_simple_captcha(driver)
            elif captcha_type == 'difficult':
                solved = self.solver_difficult_captcha(driver)
            else:
                # Тип неизвестен — пробуем сначала простую, потом сложную
                solved = self.solver_simple_captcha(driver)
                if not solved:
                    solved = self.solver_difficult_captcha(driver)

            if solved:
                time.sleep(3)
                if not self.is_captcha_page(driver):
                    logger.info(f"Капча ({captcha_type}) успешно решена")
                    return True
                else:
                    # Возможно, появилась сложная капча после простой
                    new_type = self.get_captcha_type(driver)
                    logger.info(f"Капча ещё присутствует, новый тип: {new_type}")
                    if new_type == 'difficult' and captcha_type == 'simple':
                        logger.info("Появилась сложная капча после чекбокса, решаем...")
                        if self.solver_difficult_captcha(driver):
                            time.sleep(3)
                            if not self.is_captcha_page(driver):
                                logger.info("Сложная капча после чекбокса решена")
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

        if self.is_captcha_page(driver):
            logger.warning("Капча все еще присутствует, повтор решения...")
            return self.handle_captcha(driver, url)

        logger.info("Капча успешно решена, продолжается парсинг")
        return True

    def get_category_from_user(self):
        print("\nВвод URL категории")
        while True:
            url = input("Введите URL категории ChipDip: ").strip()
            if url.startswith(BASE_URL) and '/catalog/' in url:
                category_name = self.extract_category_name_from_url(url)
                return url, category_name
            else:
                print(f"URL должен начинаться с {BASE_URL} и содержать '/catalog/'")

    def extract_category_name_from_url(self, url):
        try:
            match = re.search(r'/catalog/([^/?]+)', url)
            if match:
                name = match.group(1)
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
                    logger.info(f"Выбран диапазон страниц: {start_page}-{end_page}")
                    return start_page, end_page
                else:
                    print("Ошибка: начальная страница должна быть не меньше 1 и не больше конечной")
            except ValueError:
                print("Ошибка: введите целые числа")

    def add_max_items_param(self, url):
        if '?' in url:
            if 'ps=' in url:
                url = re.sub(r'ps=[^&]+', 'ps=x3', url)
            else:
                url += '&ps=x3'
        else:
            url += '?ps=x3'
        return url

    def get_product_features_with_retry(self, driver, url, category, max_retries=3):
        for attempt in range(max_retries):
            try:
                driver.get(url)

                if self.is_captcha_page(driver):
                    logger.warning(f"Капча обнаружена при загрузке товара (попытка {attempt + 1}/{max_retries})")
                    self.handle_captcha(driver, url)
                    continue

                WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'h1')))
                self.human_like_actions(driver)

                name_product = driver.find_element(By.TAG_NAME, 'h1')
                product_name = name_product.text
                logger.info(f"Товар: {product_name}")

                tu_number = self.extract_tu_number(driver)
                manufacturer = self.extract_brand(driver)
                voltage = self.extract_voltage(driver)

                try:
                    button = driver.find_element(By.CSS_SELECTOR,
                                                 'span.link.link_pseudo.link_showhide.f-size.with-icon.with-icon_right.with-icon_sort-down')
                    driver.execute_script("arguments[0].click();", button)
                except NoSuchElementException:
                    pass

                names = driver.find_elements(By.CLASS_NAME, 'product__param-name')
                values = driver.find_elements(By.CLASS_NAME, 'product__param-value')
                params = {}
                for name, value in zip(names, values):
                    param_name = name.text.strip().lower()
                    param_value = value.text.strip()
                    params[param_name] = param_value

                article = self.extract_article_from_url(url)

                component = ElectronicComponent(
                    name=product_name,
                    tu_number=tu_number,
                    manufacturer=manufacturer,
                    supplier=params.get('поставщик', 'Чип и Дип'),
                    article=article,
                    url=url,
                    category=category,
                    voltage=voltage
                )

                return component

            except TimeoutException:
                if self.is_captcha_page(driver):
                    logger.warning(f"Таймаут из-за капчи (попытка {attempt + 1}/{max_retries})")
                    self.handle_captcha(driver, url)
                else:
                    logger.error(f"Таймаут при загрузке товара {url}")
                    if attempt == max_retries - 1:
                        return None

            except Exception as e:
                logger.error(f"Ошибка парсинга товара {url} (попытка {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    return None

        return None

    def extract_tu_number(self, driver):
        try:
            xpaths = [
                "//*[contains(text(), 'Номенклатурный номер')]/following-sibling::*",
                "//*[contains(text(), 'Номенклатурный номер')]/../*[last()]",
                "//*[contains(text(), 'Номенклатурный номер')]/..//*[contains(@class, 'product__code')]",
            ]

            for xpath in xpaths:
                try:
                    elements = driver.find_elements(By.XPATH, xpath)
                    for element in elements:
                        text = element.text.strip()
                        numbers = re.findall(r'\d+', text)
                        if numbers and len(numbers[0]) >= 3:
                            return numbers[0]
                except:
                    continue

            selectors = [
                "div.product__code",
                "span.product__code",
                ".product__code",
                "div[class*='code']",
                "span[class*='code']",
            ]

            for selector in selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        text = element.text.strip()
                        numbers = re.findall(r'\d+', text)
                        if numbers and len(numbers[0]) >= 3:
                            if not re.search(r'[a-zA-Z]', text):
                                return numbers[0]
                except:
                    continue

            try:
                page_text = driver.find_element(By.TAG_NAME, "body").text
                pattern = r'Номенклатурный номер[\s:\-]*(\d+)'
                match = re.search(pattern, page_text, re.IGNORECASE)
                if match:
                    return match.group(1)
            except:
                pass

        except Exception as e:
            logger.warning(f"Не удалось извлечь ТУ номер: {e}")

        return ""

    def extract_voltage(self, driver):
        """Метод извлечения напряжения"""
        try:
            voltage = self._extract_from_parameters_table_improved(driver)
            if voltage:
                return voltage
            voltage = self._extract_from_description_improved(driver)
            if voltage:
                return voltage
            voltage = self._extract_from_other_sections_improved(driver)
            if voltage:
                return voltage

        except Exception as e:
            logger.warning(f"Ошибка при извлечении напряжения: {e}")

        return ""

    def _extract_from_parameters_table_improved(self, driver):
        try:
            param_rows = driver.find_elements(By.CSS_SELECTOR, ".product__param-row")

            voltage_patterns = [
                "напряжение питания",
                "supply voltage",
                "voltage - supply",
                "voltage supply",
                "working voltage",
                "operating voltage",
                "питание",
                "vcc",
                "vdd",
                "input voltage",
                "рабочее напряжение",
                "входное напряжение",
                "напряжение"
            ]

            for row in param_rows:
                try:
                    name_elem = row.find_element(By.CSS_SELECTOR, ".product__param-name")
                    value_elem = row.find_element(By.CSS_SELECTOR, ".product__param-value")

                    param_name = name_elem.text.strip().lower()
                    param_value = value_elem.text.strip()

                    # Проверяем совпадение с нашими паттернами
                    for pattern in voltage_patterns:
                        if pattern in param_name:
                            voltage = self._clean_voltage_string_improved(param_value)
                            if voltage:
                                logger.info(f"Найдено напряжение в таблице параметров: {voltage} из '{param_value}'")
                                return voltage

                    # Дополнительная проверка для английских названий
                    if any(word in param_name for word in ['voltage', 'vcc', 'vdd']):
                        voltage = self._clean_voltage_string_improved(param_value)
                        if voltage:
                            logger.info(f"Найдено напряжение (англ): {voltage} из '{param_value}'")
                            return voltage

                except Exception as e:
                    logger.debug(f"Ошибка в строке параметра: {e}")
                    continue

        except Exception as e:
            logger.debug(f"Ошибка при поиске в таблице параметров: {e}")

        return ""

    def _extract_from_description_improved(self, driver):
        try:
            description_selectors = [
                ".product__description",
                ".product-details__description",
                ".description",
                "[itemprop='description']",
                ".product-info__description",
                ".product__text"
            ]

            for selector in description_selectors:
                try:
                    desc_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for desc_element in desc_elements:
                        desc_text = desc_element.text
                        voltage = self._find_voltage_in_text_improved(desc_text)
                        if voltage:
                            logger.info(f"Найдено напряжение в описании: {voltage}")
                            return voltage
                except:
                    continue

        except Exception as e:
            logger.debug(f"Ошибка при поиске в описании: {e}")

        return ""

    def _extract_from_other_sections_improved(self, driver):
        """Улучшенное извлечение из других секций"""
        try:
            # Ищем в заголовках и основных блоках
            selectors = [
                "h1", "h2", "h3",
                ".product__title",
                ".product__header",
                ".product-info",
                ".specifications"
            ]

            for selector in selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        text = element.text
                        voltage = self._find_voltage_in_text_improved(text)
                        if voltage:
                            logger.info(f"Найдено напряжение в {selector}: {voltage}")
                            return voltage
                except:
                    continue

            # Поиск по всему тексту страницы как последний вариант
            body_text = driver.find_element(By.TAG_NAME, "body").text
            voltage = self._find_voltage_in_text_improved(body_text)
            if voltage:
                logger.info(f"Найдено напряжение в общем тексте страницы: {voltage}")
                return voltage

        except Exception as e:
            logger.debug(f"Ошибка при поиске в других секциях: {e}")

        return ""

    def _find_voltage_in_text_improved(self, text):
        """Улучшенный поиск напряжения в тексте"""
        if not text:
            return ""

        # Паттерны для поиска напряжения
        patterns = [
            # Паттерны для диапазонов
            r'([\d\.,]+)\s*[VВВ]\s*[~\-–—]\s*([\d\.,]+)\s*[VВВ]',
            r'([\d\.,]+)\s*[VВВ]\s*(?:to|до)\s*([\d\.,]+)\s*[VВВ]',

            # Паттерны для сложных форматов
            r'([\d\.,]+)\s*[VВВ]\s*[~\-–—]\s*[\d\.,]+\s*[VВВ]\s*,\s*[±±]\s*[\d\.,]+\s*[VВВ]\s*[~\-–—]\s*[\d\.,]+\s*[VВВ]',

            # Паттерны для одиночных значений
            r'([\d\.,]+)\s*[VВВ](?:\s|$|,|;)',
            r'([\d\.,]+)\s*[VВВ]\s*[\(\)]',

            # Паттерны с префиксами
            r'[Vv]oltage[\s\-]*[Ss]upply[\s:\-]*([\d\.,…]+)\s*[VВВ]',
            r'[Ss]upply[\s\-]*[Vv]oltage[\s:\-]*([\d\.,…]+)\s*[VВВ]',
            r'напряжение[\s\-]*питания[\s:\-]*([\d\.,…]+)\s*[VВВ]',
            r'[Vv]cc[\s:\-]*([\d\.,…]+)\s*[VВВ]',
            r'[Vv]dd[\s:\-]*([\d\.,…]+)\s*[VВВ]',

            # Паттерны для значений в скобках или с дополнительными символами
            r'\(([\d\.,]+)\s*[VВВ]\)',
            r'\[([\d\.,]+)\s*[VВВ]\]'
        ]

        for pattern in patterns:
            try:
                matches = re.findall(pattern, text, re.IGNORECASE | re.UNICODE)
                if matches:
                    for match in matches:
                        if isinstance(match, tuple):
                            # Для диапазонов берем первое значение
                            voltage_str = match[0]
                        else:
                            voltage_str = match

                        voltage = self._clean_voltage_string_improved(voltage_str)
                        if voltage:
                            logger.info(f"Найдено напряжение по паттерну '{pattern}': {voltage} из '{voltage_str}'")
                            return voltage
            except Exception as e:
                logger.debug(f"Ошибка в паттерне {pattern}: {e}")
                continue

        return ""

    def _clean_voltage_string_improved(self, voltage_str):
        if not voltage_str:
            return ""

        try:
            clean_str = re.sub(r'[^\d\.,]', '', str(voltage_str).strip())

            if not clean_str:
                numbers = re.findall(r'[\d\.,]+', str(voltage_str))
                if numbers:
                    clean_str = numbers[0]

            clean_str = clean_str.replace(',', '.')
            parts = clean_str.split('.')
            if len(parts) > 1:
                clean_str = parts[0] + '.' + ''.join(parts[1:])
            voltage_num = float(clean_str)

            if 0.1 <= voltage_num <= 1000:
                return str(voltage_num)

        except Exception as e:
            logger.debug(f"Ошибка очистки напряжения '{voltage_str}': {e}")

        return ""

    def extract_brand(self, driver):
        try:
            try:
                brand_elements = driver.find_elements(By.CSS_SELECTOR, '[itemprop="brand"]')
                for element in brand_elements:
                    text = element.text.strip()
                    if text and len(text) > 1:
                        return text
            except:
                pass

            try:
                brand_links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/manufacturer/"]')
                for link in brand_links:
                    text = link.text.strip()
                    if text and len(text) > 1:
                        return text
            except:
                pass

            try:
                rows = driver.find_elements(By.CSS_SELECTOR, ".product__param-row")
                for row in rows:
                    try:
                        name_elem = row.find_element(By.CSS_SELECTOR, ".product__param-name")
                        value_elem = row.find_element(By.CSS_SELECTOR, ".product__param-value")
                        if name_elem and value_elem:
                            name_text = name_elem.text.strip().lower()
                            value_text = value_elem.text.strip()
                            if 'бренд' in name_text and value_text:
                                return value_text
                    except:
                        continue
            except:
                pass

        except Exception as e:
            logger.error(f"Ошибка извлечения бренда: {e}")

        return "Не определен"

    def extract_article_from_url(self, url):
        try:
            parts = url.split('/')
            for i, part in enumerate(parts):
                if part == 'product' and i + 1 < len(parts):
                    return parts[i + 1]
        except:
            pass
        return ""

    def parse_category_page_range(self, driver, category_url, category_name, start_page, end_page):
        logger.info(f"Парсинг категории: {category_name}, страницы {start_page}-{end_page}")

        category_components = []

        for page_num in range(start_page, end_page + 1):
            # Ротация прокси каждые _rotate_every страниц
            if PROXY_LIST and page_num != start_page and (page_num - start_page) % self._rotate_every == 0:
                logger.info(f"Ротация прокси (каждые {self._rotate_every} стр.)...")
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = self.setup_driver()

            if page_num == 1:
                page_url = self.add_max_items_param(category_url)
            else:
                base_url = category_url.split('?')[0]
                if '?' in category_url:
                    page_url = f"{category_url}&page={page_num}"
                else:
                    page_url = f"{category_url}?page={page_num}"
                page_url = self.add_max_items_param(page_url)

            logger.info(f"Страница {page_num} - {page_url}")

            try:
                driver.get(page_url)

                # проверяем капчу на странице категории
                if self.is_captcha_page(driver):
                    logger.warning(f"Капча обнаружена на странице категории {page_num}")
                    self.handle_captcha(driver, page_url)

                self.human_like_actions(driver)

                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "tr.with-hover a.link")))
                except TimeoutException:
                    if self.is_captcha_page(driver):
                        logger.warning(f"Таймаут из-за капчи на странице {page_num}")
                        self.handle_captcha(driver, page_url)
                        # повторяем попытку после решения капчи
                        driver.get(page_url)
                        WebDriverWait(driver, 10).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "tr.with-hover a.link")))
                    else:
                        logger.info(f"На странице {page_num} нет товаров - остановка")
                        break

                product_elements = driver.find_elements(By.CSS_SELECTOR, "tr.with-hover a.link")
                product_urls = [el.get_attribute('href') for el in product_elements]

                if not product_urls:
                    logger.info(f"На странице {page_num} нет товаров - остановка")
                    break

                logger.info(f"Найдено {len(product_urls)} товаров на странице {page_num}")

                for i, product_url in enumerate(product_urls):
                    logger.info(f"Парсинг товара {i + 1}/{len(product_urls)} на странице {page_num}")
                    component = self.get_product_features_with_retry(driver, product_url, category_name)
                    if component:
                        category_components.append(component)

            except Exception as e:
                logger.error(f"Ошибка обработки страницы {page_num}: {e}")
                continue

        logger.info(f"Категория завершена: {len(category_components)} товаров со страниц {start_page}-{end_page}")
        return category_components, driver

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
        logger.info("Запуск парсера ChipDip")
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

            components, driver = self.parse_category_page_range(driver, category_url, category_name, start_page, end_page)

            safe_category_name = "".join(c for c in category_name if c.isalnum() or c in (' ', '_', '-')).rstrip()
            safe_category_name = safe_category_name.replace(' ', '_')
            filename = f"{safe_category_name}_pages_{start_page}_to_{end_page}.json"

            self.save_results(components, filename)

            total_time = time.time() - start_time
            logger.info(f"Парсинг завершен за {total_time:.1f} секунд")

        finally:
            driver.quit()


if __name__ == "__main__":

    scraper = ChipDipScraper()
    scraper.run()
