from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException, TimeoutException
import time
import random
import json
import os
import base64
import struct
import tempfile
from dataclasses import dataclass, field
import logging
import re
from twocaptcha import TwoCaptcha
from dotenv import load_dotenv
try:
    import msvcrt
except ImportError:
    import select

import sys

load_dotenv()

# Папка где лежит сам скрипт — все пути строятся относительно неё
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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
            # host:port:user:pass — прокси с авторизацией
            proxies.append({"host": parts[0], "port": parts[1], "user": parts[2], "pass": parts[3]})
        elif len(parts) == 2:
            # host:port — прокси без авторизации
            proxies.append({"host": parts[0], "port": parts[1], "user": None, "pass": None})
        else:
            logger.warning(f"Неверный формат прокси (ожидается host:port или host:port:user:pass): {entry}")
    # Если USE_OWN_IP=true — добавляем "слот" своего IP в начало (чтобы старт шёл с него)
    if os.getenv("USE_OWN_IP", "false").lower() in ("1", "true", "yes"):
        proxies.insert(0, None)
        logger.info("Свой IP добавлен в пул прокси (USE_OWN_IP=true), старт с него")
    return proxies


PROXY_LIST = _parse_proxies()
_proxy_index = 0  # текущий индекс в PROXY_LIST


def _next_proxy():
    """Возвращает следующий прокси по кругу. None если список пуст или слот своего IP."""
    global _proxy_index
    if not PROXY_LIST:
        return None
    p = PROXY_LIST[_proxy_index % len(PROXY_LIST)]
    _proxy_index += 1
    return p


def _remove_proxy(proxy):
    """Удаляет забаненный прокси из списка."""
    try:
        PROXY_LIST.remove(proxy)
        if proxy is None:
            logger.warning("Слот 'свой IP' удалён из пула прокси.")
        else:
            logger.warning(f"Прокси {proxy['host']}:{proxy['port']} удалён из списка. Осталось: {len(PROXY_LIST)}")
    except ValueError:
        pass


def check_keypress():
    """Проверяет была ли нажата клавиша Enter"""
    if 'msvcrt' in sys.modules:
        if sys.modules['msvcrt'].kbhit():
            if sys.modules['msvcrt'].getch() == b'\r':
                return True
    elif 'select' in sys.modules:
        dr, dw, de = select.select([sys.stdin], [], [], 0)
        if dr:
            char = sys.stdin.read(1)
            if char == '\n':
                return True
    return False


@dataclass
class ElectronicComponent:
    name: str
    tu_number: str  # Номенклатурный номер ChipDip
    manufacturer: str  # Бренд (производитель)
    supplier: str
    source: str = "chipdip.ru"
    article: str = ""  # Артикул из URL
    url: str = ""
    category: str = ""
    voltage: str = ""
    all_specs: dict = field(default_factory=dict)


class ChipDipScraper:
    def __init__(self):
        self.components = []
        self.create_directories()
        self.solver = TwoCaptcha(API_KEY)
        self._rotate_every = 1  # менять прокси каждые N страниц категории
        self._autosave_file = None  # путь к файлу автосохранения текущей сессии
        self._using_own_ip = False  # True когда активен слот "свой IP"

    def create_directories(self):
        directories = [
            os.path.join(BASE_DIR, 'data'),
            os.path.join(BASE_DIR, 'data', 'results')
        ]
        for directory in directories:
            os.makedirs(directory, exist_ok=True)

    def _create_proxy_extension(self, host, port, username, password):
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

    def setup_driver(self):
        options = Options()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--log-level=3")
        options.add_argument("--disable-logging")
        options.add_argument("--disable-webrtc")
        options.add_argument("--webrtc-ip-handling-policy=disable_non_proxied_udp")
        options.add_argument("--enforce-webrtc-ip-permission-check")
        options.add_argument("--window-size=1920,1080")
        options.add_experimental_option('excludeSwitches', ['enable-logging'])

        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
        ]
        options.add_argument(f"--user-agent={random.choice(user_agents)}")

        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)

        if PROXY_LIST:
            p = _next_proxy()
            if p is None:
                # Слот "свой IP" — работаем без прокси
                self._current_proxy = None
                self._using_own_ip = True
                logger.info("Используем свой IP (без прокси)")
            elif p["user"] is not None:
                # Прокси с авторизацией — нужно расширение
                self._current_proxy = p
                self._using_own_ip = False
                ext = self._create_proxy_extension(p["host"], p["port"], p["user"], p["pass"])
                options.add_argument(f"--load-extension={ext}")
                logger.info(f"Прокси с авторизацией: {p['host']}:{p['port']} (осталось {len(PROXY_LIST)})")
            else:
                # Прокси без авторизации — достаточно --proxy-server
                self._current_proxy = p
                self._using_own_ip = False
                options.add_argument(f"--proxy-server=http://{p['host']}:{p['port']}")
                logger.info(f"Прокси без авторизации: {p['host']}:{p['port']} (осталось {len(PROXY_LIST)})")
        else:
            self._current_proxy = None
            self._using_own_ip = False
            logger.info("Список прокси пуст, работаем без прокси")

        driver = webdriver.Edge(options=options)
        driver.set_page_load_timeout(30)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return driver

    def random_sleep(self, min_sec=2.0, max_sec=5.0):
        delay = random.uniform(min_sec, max_sec)
        time.sleep(delay)

    def human_like_actions(self, driver):
        try:
            scroll_height = driver.execute_script("return document.body.scrollHeight")
            if scroll_height > 500:
                random_scroll = random.randint(300, min(800, scroll_height))
                driver.execute_script(f"window.scrollTo(0, {random_scroll});")
                time.sleep(random.uniform(0.5, 1.5))
        except:
            pass

    def is_captcha_page(self, driver):
        try:
            # chipdip редиректит на /forms/captcha2
            if 'captcha' in driver.current_url.lower():
                return True
            # Проверяем наличие контейнера капчи на странице
            if driver.find_elements(By.CSS_SELECTOR, "div#captcha-container, div.captcha-w, form#captcha_form"):
                return True
            return False
        except:
            return False

    def is_proxy_blocked(self, driver):
        """Проверяет жёсткий бан прокси. Срабатывает ТОЛЬКО на страницах-заглушках."""
        try:
            title = driver.title.lower()
            # Страница бана chipdip имеет характерный title и очень мало контента
            if "недоступен" in title or "access denied" in title or "403" in title:
                return True
            # Проверяем body только если страница короткая (заглушка)
            body_text = driver.find_element(By.TAG_NAME, "body").text.strip()
            if len(body_text) < 500:
                hard_ban = [
                    "запретил доступ из вашей сети",
                    "access denied",
                    "403 forbidden",
                ]
                body_lower = body_text.lower()
                for sign in hard_ban:
                    if sign in body_lower:
                        return True
            return False
        except:
            return False

    def is_browser_check(self, driver):
        """Проверяет проверку браузера (Cloudflare). Только на страницах-заглушках."""
        try:
            title = driver.title.lower()
            # Cloudflare/DDoS-Guard ставят характерный title
            if "just a moment" in title or "checking" in title or "ddos" in title:
                return True
            # Проверяем body только если страница короткая
            body_text = driver.find_element(By.TAG_NAME, "body").text.strip()
            if len(body_text) < 500:
                check_signs = [
                    "checking your browser",
                    "please wait a few seconds",
                    "ddos-guard",
                ]
                body_lower = body_text.lower()
                for sign in check_signs:
                    if sign in body_lower:
                        return True
            return False
        except:
            return False

    def wait_for_browser_check(self, driver, timeout=30):
        """Ждёт прохождения проверки браузера. True — прошло, False — не прошло."""
        logger.info("Проверка браузера (Cloudflare), ждём до %d сек...", timeout)
        for tick in range(timeout):
            time.sleep(1)
            
            try:
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                for iframe in iframes:
                    try:
                        driver.switch_to.frame(iframe)
                        checkboxes = driver.find_elements(By.CSS_SELECTOR, "#checkbox, .checkbox, #captcha-checkbox, [type='checkbox']")
                        if checkboxes:
                            checkbox = checkboxes[0]
                            try:
                                ActionChains(driver).move_to_element(checkbox).click().perform()
                                logger.info("Кликнули по чекбоксу в iframe")
                                time.sleep(3)
                            except:
                                pass
                    except:
                        pass
                    finally:
                        driver.switch_to.default_content()
            except:
                try:
                    driver.switch_to.default_content()
                except:
                    pass

            if not self.is_browser_check(driver):
                logger.info("✓ Проверка браузера пройдена за %d сек", tick + 1)
                return True
            if tick % 5 == 4:
                logger.info("  %d/%d сек, всё ещё проверка...", tick + 1, timeout)
        logger.warning("Проверка браузера не прошла за %d сек", timeout)
        return False

    def switch_proxy(self, driver):
        """Удаляет текущий забаненный прокси и создаёт драйвер с новым"""
        if not PROXY_LIST:
            logger.error("Список прокси пуст, переключение невозможно")
            return driver

        # Удаляем забаненный прокси
        if self._current_proxy:
            _remove_proxy(self._current_proxy)
        elif self._using_own_ip:
            # Слот "свой IP" тоже банят — удаляем None из пула
            _remove_proxy(None)
            self._using_own_ip = False

        if not PROXY_LIST:
            logger.error("Все прокси забанены!")
            return driver

        logger.warning("Переключаемся на следующий прокси...")
        try:
            driver.quit()
        except Exception:
            pass
        time.sleep(2)
        return self.setup_driver()

    def get_captcha_type(self, driver):
        """Определяет тип капчи по наличию iframe"""
        try:
            # Делаем проверку видимости элемента (чтобы не реагировать на скрытые iframe)
            advanced_frames = driver.find_elements(By.CSS_SELECTOR,
                "div.SmartCaptcha-Overlay iframe[data-testid='advanced-iframe'], "
                "iframe[data-testid='advanced-iframe']")
            if any(frame.is_displayed() for frame in advanced_frames):
                return 'difficult'

            simple_frames = driver.find_elements(By.CSS_SELECTOR, "iframe[data-testid='checkbox-iframe']")
            if any(frame.is_displayed() for frame in simple_frames):
                return 'simple'

            # Если не видим сразу — ждём до 5 секунд
            for _ in range(5):
                time.sleep(1)
                advanced_frames = driver.find_elements(By.CSS_SELECTOR,
                    "div.SmartCaptcha-Overlay iframe[data-testid='advanced-iframe'], "
                    "iframe[data-testid='advanced-iframe']")
                if any(frame.is_displayed() for frame in advanced_frames):
                    return 'difficult'
                simple_frames = driver.find_elements(By.CSS_SELECTOR, "iframe[data-testid='checkbox-iframe']")
                if any(frame.is_displayed() for frame in simple_frames):
                    return 'simple'
        except:
            pass
        return None

    def solver_simple_captcha(self, driver):
        try:
            time.sleep(2)
            # Капча внутри iframe — нужно переключиться в него
            iframe = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "iframe[data-testid='checkbox-iframe']"))
            )
            logger.info("Найден checkbox-iframe, переключаемся...")
            driver.switch_to.frame(iframe)

            checkbox = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "js-button"))
            )
            logger.info("Найдена кнопка js-button внутри iframe")

            # Пробуем несколько способов клика (на случай, если первый "проглотится" без эффекта)
            success = False
            for attempt in range(3):
                try:
                    if attempt == 0:
                        # 1. ActionChains с небольшим ожиданием
                        time.sleep(1)
                        ActionChains(driver).move_to_element(checkbox).click().perform()
                    elif attempt == 1:
                        # 2. Обычный клик
                        checkbox.click()
                    else:
                        # 3. JavaScript клик
                        driver.execute_script("arguments[0].click();", checkbox)
                    success = True
                    break
                except Exception as e:
                    logger.warning(f"Способ клика {attempt + 1} не сработал: {e}")
                    time.sleep(1)

            if not success:
               logger.warning("Все попытки клика в checkbox неудачны. Пробуем JS напоследок.")
               driver.execute_script("arguments[0].click();", checkbox)

            time.sleep(3)
            driver.switch_to.default_content()
            logger.info("Простая капча — клики выполнены")
            return True
        except Exception as e:
            logger.warning(f"Ошибка решения простой капчи: {e}")
            driver.switch_to.default_content()
            return False

    def get_captcha_images(self, driver):
        try:
            time.sleep(2)
            # advanced-iframe находится в div.SmartCaptcha-Overlay (вне div.captcha-w)
            iframe = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "iframe[data-testid='advanced-iframe']"))
            )
            logger.info("Найден advanced-iframe, переключаемся...")
            driver.switch_to.frame(iframe)

            # Ждём загрузки содержимого iframe
            main_img_element = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.AdvancedCaptcha-ImageWrapper img"))
            )
            main_image = main_img_element.screenshot_as_base64

            try:
                instruction_img_element = driver.find_element(By.CSS_SELECTOR, "img.TaskImage")
                instruction_image = instruction_img_element.screenshot_as_base64
            except:
                instruction_image = main_image

            logger.info("Получены изображения сложной капчи из iframe")
            # НЕ делаем switch_to.default_content() — нужен для кликов и submit
            return main_image, instruction_image, main_img_element
        except Exception as e:
            logger.warning(f"Ошибка получения изображений: {e}")
            driver.switch_to.default_content()
            return None, None, None

    def parse_coordinates(self, coords_string):
        coords_string = coords_string.replace("coordinates:", "").strip()
        coordinates = []
        for pair in coords_string.split(";"):
            if "," not in pair:
                continue
            parts = pair.split(",")
            x = int(parts[0].split("=")[1])
            y = int(parts[1].split("=")[1])
            coordinates.append({"x": x, "y": y})
        return coordinates

    def click_silhouettes(self, driver, coordinates):
        try:
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

            logger.info("✓ Все клики выполнены")
            return True

        except Exception as e:
            logger.error(f"Ошибка кликов: {e}")
            return False

    def refresh_captcha(self, driver):
        """Нажимает кнопку 'Обновить задание' внутри advanced-iframe"""
        try:
            refresh_btn = driver.find_element(By.CSS_SELECTOR, "button[data-testid='refresh']")
            refresh_btn.click()
            logger.info("Капча обновлена (кнопка refresh)")
            time.sleep(3)  # Ждём загрузки нового задания
            return True
        except Exception as e:
            logger.warning(f"Не удалось нажать refresh: {e}")
            return False

    def solver_difficult_captcha(self, driver):
        last_captcha_id = None

        for inner_attempt in range(3):
            if check_keypress():
                logger.info("! Ручной пропуск капчи (нажат Enter) !")
                return True

            try:
                if inner_attempt > 0:
                    logger.info(f"Внутренняя попытка {inner_attempt + 1}/3 сложной капчи")
                    # Репортим предыдущее неверное решение в 2captcha
                    if last_captcha_id:
                        try:
                            self.solver.report(last_captcha_id, False)
                            logger.info(f"Репорт об ошибке отправлен в 2captcha (id={last_captcha_id})")
                        except Exception as e:
                            logger.warning(f"Не удалось отправить репорт: {e}")

                    # Обновляем задание капчи
                    self.refresh_captcha(driver)

                # get_captcha_images переключает в advanced-iframe и остаётся в нём
                main_img, instruction_img, _ = self.get_captcha_images(driver)
                if not main_img or not instruction_img:
                    logger.warning("Не получили изображения сложной капчи")
                    driver.switch_to.default_content()
                    time.sleep(3)
                    continue

                logger.info(f"Отправка в 2captcha (попытка {inner_attempt + 1})...")
                result = self.solver.coordinates(
                    file=f"data:image/png;base64,{main_img}",
                    hintImg=f"data:image/png;base64,{instruction_img}"
                )
                
                if check_keypress():
                    logger.info("! Ручной пропуск капчи (нажат Enter) !")
                    return True
                    
                last_captcha_id = result.get('captchaId') or result.get('id')
                coordinates = self.parse_coordinates(result['code'])
                logger.info(f"2captcha ответил: {result['code']} (id={last_captcha_id})")

                # Кликаем — click_silhouettes сам переключается default->iframe->default->iframe
                # Скриншот делаем пока мы внутри iframe (после get_captcha_images)
                try:
                    driver.save_screenshot(f"debug_captcha_attempt_{inner_attempt+1}.png")
                    logger.info(f"Скриншот сохранён: debug_captcha_attempt_{inner_attempt+1}.png")
                except:
                    pass

                # Кликаем — мы внутри advanced-iframe
                self.click_silhouettes(driver, coordinates)
                time.sleep(1)

                # Submit внутри iframe
                try:
                    submit_btn = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-testid='submit']"))
                    )
                    submit_btn.click()
                    logger.info("Submit нажат, ждём реакции капчи...")
                except TimeoutException:
                    driver.execute_script(
                        "document.querySelector('button[data-testid=\"submit\"]').click();"
                    )

                # Ждём 5 сек — появится либо ошибка ("Попробуйте ещё раз") либо закроется overlay
                for _ in range(5):
                    if check_keypress():
                        logger.info("! Ручной пропуск капчи (нажат Enter) !")
                        return True
                    time.sleep(1)
                    
                driver.switch_to.default_content()

                # Проверяем — закрылся ли overlay (капча пройдена)
                overlay_still_visible = driver.find_elements(
                    By.CSS_SELECTOR, "div.SmartCaptcha-Overlay_visible, div.SmartCaptcha-Overlay"
                )
                if not overlay_still_visible:
                    logger.info("✓ Overlay исчез — сложная капча пройдена!")
                    if last_captcha_id:
                        try:
                            self.solver.report(last_captcha_id, True)
                        except:
                            pass
                    return True
                    
                try:
                    if "страница не найдена" in driver.title.lower() or "страница не найдена" in driver.page_source.lower():
                        logger.info("Появилась 'Страница не найдена' — считаем пройденным/пропущенным")
                        return True
                except:
                    pass

                # Overlay ещё есть — решение неверное, пробуем снова
                logger.warning(f"Overlay ещё виден после попытки {inner_attempt + 1} — решение неверное")
                # Переключаемся обратно в iframe для следующей попытки (refresh)
                try:
                    iframe = driver.find_element(By.CSS_SELECTOR, "iframe[data-testid='advanced-iframe']")
                    driver.switch_to.frame(iframe)
                except:
                    pass
                time.sleep(2)

            except Exception as e:
                logger.error(f"Ошибка в попытке {inner_attempt + 1} сложной капчи: {e}")
                driver.switch_to.default_content()
                time.sleep(3)

        logger.error("Все 3 внутренние попытки сложной капчи исчерпаны")
        driver.switch_to.default_content()
        return False

    def _wait_for_redirect(self, driver, timeout=25):
        """Ждёт редиректа с captcha-страницы. Возвращает True если произошёл."""
        logger.info(f"Ждём редиректа до {timeout} сек...")
        for tick in range(timeout):
            if check_keypress():
                logger.info("! Ручной пропуск ожидания (нажат Enter) !")
                return True
                
            time.sleep(1)
            if 'captcha' not in driver.current_url.lower():
                logger.info(f"✓ Редирект на: {driver.current_url}")
                return True
            if tick % 5 == 4:
                logger.info(f"  {tick+1}/{timeout} сек | всё ещё на капче...")
        logger.warning(f"Редирект не произошёл за {timeout} сек")
        return False

    def handle_captcha(self, driver, url):
        logger.info("Обнаружена капча, начинаем автоматическое решение...")
        logger.info(f"Текущий URL: {driver.current_url}")

        max_attempts = 2  # solver_difficult_captcha сам делает 3 попытки внутри
        for attempt in range(max_attempts):
            if check_keypress():
                logger.info("! Ручной пропуск капчи (нажат Enter) !")
                return True
                
            logger.info(f"--- Попытка {attempt + 1}/{max_attempts} ---")
            
            try:
                if "страница не найдена" in driver.title.lower() or "страница не найдена" in driver.page_source.lower():
                    logger.info("Сразу найдено 'Страница не найдена' — пропускаем")
                    return True
            except:
                pass

            captcha_type = self.get_captcha_type(driver)
            logger.info(f"Определён тип капчи: {captcha_type}")

            if captcha_type == 'simple':
                logger.info("Решаем простую капчу (чекбокс)...")
                if not self.solver_simple_captcha(driver):
                    logger.warning("Не удалось нажать чекбокс, пауза 5 сек...")
                    time.sleep(5)
                    continue

                logger.info("Чекбокс нажат. Ждём до 20 сек — редирект или сложная капча...")
                advanced_found = False
                for tick in range(20):
                    if check_keypress():
                        logger.info("! Ручной пропуск капчи (нажат Enter) !")
                        return True
                        
                    time.sleep(1)
                    if 'captcha' not in driver.current_url.lower():
                        logger.info(f"✓ Простая капча пройдена! URL: {driver.current_url}")
                        return True
                    overlay = driver.find_elements(By.CSS_SELECTOR,
                        "div.SmartCaptcha-Overlay, iframe[data-testid='advanced-iframe']")
                    if overlay:
                        logger.info(f"  tick {tick+1}: Появился SmartCaptcha-Overlay!")
                        advanced_found = True
                        break
                    if tick % 5 == 4:
                        logger.info(f"  tick {tick+1}/20 | URL: {driver.current_url}")

                if advanced_found:
                    logger.info("Ждём 3 сек полной загрузки iframe...")
                    time.sleep(3)
                    if self.solver_difficult_captcha(driver):
                        if self._wait_for_redirect(driver, timeout=25):
                            return True
                    logger.warning("Сложная капча после чекбокса не решена")
                else:
                    logger.warning("За 20 сек ни редиректа ни сложной капчи")

            elif captcha_type == 'difficult':
                logger.info("Решаем сложную капчу напрямую...")
                if self.solver_difficult_captcha(driver):
                    if self._wait_for_redirect(driver, timeout=25):
                        return True
                logger.warning("Сложная капча не решена")

            else:
                logger.warning("Тип капчи не определён, ждём 5 сек...")
                time.sleep(5)

            logger.warning(f"Попытка {attempt + 1} не удалась, пауза 5 сек...")
            time.sleep(5)

        # Все попытки исчерпаны — ручное решение
        logger.error("Все автоматические попытки исчерпаны!")
        print("\n===-===- КАПЧА НЕ РЕШЕНА АВТОМАТИЧЕСКИ =-==-===")
        print("Решите капчу в браузере вручную и нажмите Enter")
        input("Нажмите Enter после решения капчи...")
        time.sleep(2)

    def get_categories_from_user(self):
        """Запрашивает у пользователя список URL категорий (пустая строка = конец ввода)"""
        print("\n=== ВВОД URL КАТЕГОРИЙ ===")
        print("Вводите URL категорий по одному. Пустая строка — конец ввода.\n")
        categories = []
        idx = 1
        while True:
            url = input(f"  [{idx}] URL категории (Enter = завершить): ").strip()
            if not url:
                break
            if url.startswith(BASE_URL) and '/catalog/' in url:
                category_name = self.extract_category_name_from_url(url)
                categories.append((url, category_name))
                print(f"       ✓ {category_name}")
                idx += 1
            else:
                print(f"       ✗ URL должен начинаться с {BASE_URL} и содержать '/catalog/'")
        return categories

    def get_category_from_user(self):
        """Запрашивает у пользователя URL категории (обратная совместимость)"""
        print("\n=== ВВОД URL КАТЕГОРИИ ===")
        while True:
            url = input("Введите URL категории ChipDip: ").strip()
            if url.startswith(BASE_URL) and '/catalog/' in url:
                category_name = self.extract_category_name_from_url(url)
                return url, category_name
            else:
                print(f"URL должен начинаться с {BASE_URL} и содержать '/catalog/'")

    def extract_category_name_from_url(self, url):
        """Извлекает название категории из URL"""
        try:
            # Пытаемся извлечь название из URL
            match = re.search(r'/catalog/([^/?]+)', url)
            if match:
                name = match.group(1)
                # Заменяем дефисы на пробелы и делаем первую букву заглавной
                name = name.replace('-', ' ').title()
                return name
        except:
            pass
        return "Custom_Category"

    def get_page_range_from_user(self):
        """Запрашивает у пользователя диапазон страниц для парсинга"""
        print("\n=== ДИАПАЗОН СТРАНИЦ ===")
        print("Введите диапазон страниц для парсинга (включительно)")

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

    def get_product_features(self, driver, url, category):
        try:
            try:
                self.random_sleep(1, 3)
                driver.get(url)
            except TimeoutException:
                logger.warning(f"Товар не загрузился за 30 сек — скип: {url}")
                return None
            time.sleep(1)

            # Проверка браузера — ждём
            if self.is_browser_check(driver):
                self.wait_for_browser_check(driver, timeout=15)

            # Если прокси забанен — не пробуем дальше
            if self.is_proxy_blocked(driver):
                logger.warning(f"Прокси забанен при загрузке товара {url}")
                return None

            # Сразу проверяем капчу (chipdip редиректит на /forms/captcha2)
            if self.is_captcha_page(driver):
                logger.warning(f"Капча обнаружена при загрузке товара")
                self.handle_captcha(driver, url)
                # После решения капчи повторно загружаем нужную страницу
                if 'captcha' in driver.current_url.lower():
                    try:
                        driver.get(url)
                    except TimeoutException:
                        logger.warning(f"Товар не загрузился за 30 сек после капчи — скип: {url}")
                        return None
                    time.sleep(2)

            if random.random() > 0.3:
                self.human_like_actions(driver)

            try:
                name_product = driver.find_element(By.TAG_NAME, 'h1')
                product_name = name_product.text
                logger.info(f"Product: {product_name}")
            except NoSuchElementException:
                if self.is_captcha_page(driver):
                    self.handle_captcha(driver, url)
                return self.get_product_features(driver, url, category)

            # Извлекаем номенклатурный номер (ТУ)
            tu_number = self.extract_tu_number(driver)

            # Извлекаем бренд (производителя)
            manufacturer = self.extract_brand(driver)
            logger.info(f"Manufacturer extracted: {manufacturer}")

            # Извлекаем напряжение
            voltage = self.extract_voltage(driver)

            # --- БЛОК СБОРА ВСЕХ ПАРАМЕТРОВ ---
            all_specs = {}

            # 1. Раскрываем список "Показать еще", если он есть
            try:
                expand_button = driver.find_elements(By.CSS_SELECTOR, 'span.link_showhide')
                if expand_button:
                    driver.execute_script("arguments[0].click();", expand_button[0])
                    time.sleep(0.5)
            except Exception:
                pass

            # 2. Собираем все строки из таблицы параметров
            try:
                rows = driver.find_elements(By.CSS_SELECTOR, "table#productparams tr")

                for row in rows:
                    try:
                        name_el = row.find_element(By.CLASS_NAME, 'product__param-name')
                        value_el = row.find_element(By.CLASS_NAME, 'product__param-value')

                        p_name = name_el.text.strip().replace(':', '')
                        p_value = value_el.text.strip()

                        if p_name and p_value:
                            all_specs[p_name] = p_value
                    except NoSuchElementException:
                        continue
            except Exception as e:
                logger.warning(f"Ошибка сбора параметров: {e}")

            # Определяем поставщика
            names = driver.find_elements(By.CLASS_NAME, 'product__param-name')
            values = driver.find_elements(By.CLASS_NAME, 'product__param-value')
            supplier = "Чип и Дип"
            for name, value in zip(names, values):
                if 'поставщик' in name.text.lower():
                    supplier = value.text.strip()
                    break

            # Оставляем артикул из URL как было
            article = self.extract_article_from_url(url)

            component = ElectronicComponent(
                name=product_name,
                tu_number=tu_number,
                manufacturer=manufacturer,
                supplier=supplier,
                article=article,
                url=url,
                category=category,
                voltage=voltage,
                all_specs=all_specs
            )

            return component

        except Exception as e:
            logger.error(f"Error parsing product {url}: {e}")
            return None

    def extract_tu_number(self, driver):
        """Извлекает номенклатурный номер (ТУ) со страницы товара"""
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

                    for pattern in voltage_patterns:
                        if pattern in param_name:
                            voltage = self._clean_voltage_string_improved(param_value)
                            if voltage:
                                logger.info(f"Найдено напряжение в таблице параметров: {voltage} из '{param_value}'")
                                return voltage

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

        patterns = [
            r'([\d\.,]+)\s*[VВВ]\s*[~\-–—]\s*([\d\.,]+)\s*[VВВ]',
            r'([\d\.,]+)\s*[VВВ]\s*(?:to|до)\s*([\d\.,]+)\s*[VВВ]',
            r'([\d\.,]+)\s*[VВВ]\s*[~\-–—]\s*[\d\.,]+\s*[VВВ]\s*,\s*[±±]\s*[\d\.,]+\s*[VВВ]\s*[~\-–—]\s*[\d\.,]+\s*[VВВ]',
            r'([\d\.,]+)\s*[VВВ](?:\s|$|,|;)',
            r'([\d\.,]+)\s*[VВВ]\s*[\(\)]',
            r'[Vv]oltage[\s\-]*[Ss]upply[\s:\-]*([\d\.,…]+)\s*[VВВ]',
            r'[Ss]upply[\s\-]*[Vv]oltage[\s:\-]*([\d\.,…]+)\s*[VВВ]',
            r'напряжение[\s\-]*питания[\s:\-]*([\d\.,…]+)\s*[VВВ]',
            r'[Vv]cc[\s:\-]*([\d\.,…]+)\s*[VВВ]',
            r'[Vv]dd[\s:\-]*([\d\.,…]+)\s*[VВВ]',
            r'\(([\d\.,]+)\s*[VВВ]\)',
            r'\[([\d\.,]+)\s*[VВВ]\]'
        ]

        for pattern in patterns:
            try:
                matches = re.findall(pattern, text, re.IGNORECASE | re.UNICODE)
                if matches:
                    for match in matches:
                        if isinstance(match, tuple):
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
        """Извлекает бренд (производителя) агрессивными методами"""
        try:
            # Метод 1: Поиск по itemprop="brand" (самый надежный)
            try:
                brand_elements = driver.find_elements(By.CSS_SELECTOR, '[itemprop="brand"]')
                for element in brand_elements:
                    text = element.text.strip()
                    if text and len(text) > 1:
                        logger.info(f"Found brand via itemprop: {text}")
                        return text
            except Exception as e:
                logger.debug(f"Method 1 failed: {e}")

            # Метод 2: Поиск ссылок на производителя
            try:
                brand_links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/manufacturer/"]')
                for link in brand_links:
                    text = link.text.strip()
                    if text and len(text) > 1:
                        logger.info(f"Found brand via manufacturer link: {text}")
                        return text
            except Exception as e:
                logger.debug(f"Method 2 failed: {e}")

            # Метод 3: Поиск в таблице характеристик
            try:
                # Получаем все строки
                rows = driver.find_elements(By.CSS_SELECTOR, ".product__param-row")
                for row in rows:
                    try:
                        name_elem = row.find_element(By.CSS_SELECTOR, ".product__param-name")
                        value_elem = row.find_element(By.CSS_SELECTOR, ".product__param-value")
                        if name_elem and value_elem:
                            name_text = name_elem.text.strip().lower()
                            value_text = value_elem.text.strip()
                            if 'бренд' in name_text and value_text:
                                logger.info(f"Found brand via param table: {value_text}")
                                return value_text
                    except:
                        continue
            except Exception as e:
                logger.debug(f"Method 3 failed: {e}")

            # Метод 4: Поиск по классам, связанным с брендом
            brand_selectors = [
                ".product__brand",
                ".brand",
                ".manufacturer",
                ".vendor",
                ".producer",
                ".firm",
                ".maker"
            ]

            for selector in brand_selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        text = element.text.strip()
                        if text and len(text) > 1:
                            logger.info(f"Found brand via class {selector}: {text}")
                            return text
                except:
                    continue

            # Метод 5: Поиск в заголовке или основном контенте
            try:
                # Иногда бренд указан в заголовке страницы или рядом с названием товара
                title_selectors = [
                    "h1",
                    ".product__title",
                    ".product-name",
                    ".product__header",
                    ".product__info",
                    ".product-details"
                ]

                for selector in title_selectors:
                    try:
                        element = driver.find_element(By.CSS_SELECTOR, selector)
                        text = element.text
                        # Ищем известные бренды в тексте
                        known_brands = [
                            "Analog Devices", "Texas Instruments", "STMicroelectronics",
                            "Infineon", "NXP", "ON Semiconductor", "Microchip", "Maxim",
                            "ADI", "TI", "ST", "Infineon", "NXP", "ON Semi", "Microchip",
                            "Maxim Integrated", "Renesas", "Vishay", "ROHM", "Fairchild",
                            "Intersil", "Linear Technology", "Altera", "Xilinx", "Intel",
                            "AMD", "Qualcomm", "Broadcom", "Cypress", "Silicon Labs"
                        ]
                        for brand in known_brands:
                            if brand.lower() in text.lower():
                                logger.info(f"Found known brand in text: {brand}")
                                return brand
                    except:
                        continue
            except Exception as e:
                logger.debug(f"Method 5 failed: {e}")

            # Метод 6: Поиск по XPath с текстом "Бренд"
            try:
                xpaths = [
                    "//td[contains(text(), 'Бренд')]/following-sibling::td",
                    "//th[contains(text(), 'Бренд')]/following-sibling::td",
                    "//div[contains(text(), 'Бренд')]/following-sibling::div",
                    "//span[contains(text(), 'Бренд')]/following-sibling::span"
                ]

                for xpath in xpaths:
                    try:
                        elements = driver.find_elements(By.XPATH, xpath)
                        for element in elements:
                            text = element.text.strip()
                            if text:
                                logger.info(f"Found brand via XPath: {text}")
                                return text
                    except:
                        continue
            except Exception as e:
                logger.debug(f"Method 6 failed: {e}")

            # Метод 7: Поиск в любом месте страницы по ключевым словам
            try:
                page_text = driver.find_element(By.TAG_NAME, "body").text
                brand_patterns = [
                    r"Бренд[\s:\-]*([^\n\r]+)",
                    r"Производитель[\s:\-]*([^\n\r]+)",
                    r"Manufacturer[\s:\-]*([^\n\r]+)",
                    r"Brand[\s:\-]*([^\n\r]+)"
                ]

                for pattern in brand_patterns:
                    matches = re.search(pattern, page_text, re.IGNORECASE)
                    if matches:
                        brand = matches.group(1).strip()
                        if brand and len(brand) > 1:
                            logger.info(f"Found brand via regex: {brand}")
                            return brand
            except Exception as e:
                logger.debug(f"Method 7 failed: {e}")

        except Exception as e:
            logger.error(f"All brand extraction methods failed: {e}")

        logger.warning("Brand not found")
        return "Не определен"

    def extract_article_from_url(self, url):
        """Извлекает артикул из URL (как было изначально)"""
        try:
            parts = url.split('/')
            for i, part in enumerate(parts):
                if part == 'product' and i + 1 < len(parts):
                    return parts[i + 1]
        except:
            pass
        return ""

    def add_max_items_param(self, url):
        """Добавляет параметр ps=x3 для максимального количества товаров на странице"""
        if '?' in url:
            if 'ps=' in url:
                url = re.sub(r'ps=[^&]+', 'ps=x3', url)
            else:
                url += '&ps=x3'
        else:
            url += '?ps=x3'
        return url

    def parse_category_page_range(self, driver, category_url, category_name, start_page, end_page, autosave_filepath=None):
        """Парсит указанный диапазон страниц категории.
        Если end_page=None — парсит все страницы до конца."""
        if end_page is None:
            logger.info(f"Parsing category: {category_name}, pages {start_page}-ALL")
        else:
            logger.info(f"Parsing category: {category_name}, pages {start_page}-{end_page}")

        category_components = []
        _products_since_restart = 0
        _RESTART_EVERY = 100  # перезапуск драйвера каждые N товаров для очистки памяти

        page_num = start_page
        while True:
            # Проверяем лимит страниц (если задан)
            if end_page is not None and page_num > end_page:
                break

            # Ротация прокси каждые _rotate_every страниц
            if PROXY_LIST and page_num != start_page and (page_num - start_page) % self._rotate_every == 0:
                logger.info(f"Ротация прокси (каждые {self._rotate_every} стр.)...")
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = self.setup_driver()
                _products_since_restart = 0

            # Перезапуск драйвера для очистки памяти (если прокси не ротируются)
            if not PROXY_LIST and _products_since_restart >= _RESTART_EVERY:
                logger.info(f"Перезапуск драйвера для очистки памяти ({_products_since_restart} товаров)")
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = self.setup_driver()
                _products_since_restart = 0

            if page_num == 1:
                page_url = self.add_max_items_param(category_url)
            else:
                if '?' in category_url:
                    page_url = f"{category_url}&page={page_num}"
                else:
                    page_url = f"{category_url}?page={page_num}"
                page_url = self.add_max_items_param(page_url)

            logger.info(f"Page {page_num} - {page_url}")

            try:
                try:
                    driver.get(page_url)
                except TimeoutException:
                    logger.warning(f"Страница {page_num} не загрузилась за 30 сек — скип")
                    page_num += 1
                    continue
                time.sleep(2)

                # Проверка браузера (Cloudflare) — ждём, если не прошло — меняем прокси
                if self.is_browser_check(driver):
                    if not self.wait_for_browser_check(driver, timeout=30):
                        if PROXY_LIST:
                            driver = self.switch_proxy(driver)
                            try:
                                driver.get(page_url)
                            except TimeoutException:
                                logger.warning(f"Страница {page_num} не загрузилась после смены прокси — скип")
                                page_num += 1
                                continue
                            time.sleep(3)

                # Жёсткий бан — удаляем прокси и пробуем другие
                while self.is_proxy_blocked(driver):
                    if not PROXY_LIST:
                        logger.error("Все прокси забанены, продолжаем без прокси")
                        break
                    driver = self.switch_proxy(driver)
                    try:
                        driver.get(page_url)
                    except TimeoutException:
                        continue
                    time.sleep(3)
                    if self.is_browser_check(driver):
                        if not self.wait_for_browser_check(driver, timeout=30):
                            continue

                # Проверяем капчу сразу после загрузки страницы категории
                if self.is_captcha_page(driver):
                    logger.warning(f"Капча на странице категории {page_num}")
                    self.handle_captcha(driver, page_url)
                    if 'captcha' in driver.current_url.lower():
                        try:
                            driver.get(page_url)
                        except TimeoutException:
                            logger.warning(f"Страница {page_num} не загрузилась после капчи — скип")
                            page_num += 1
                            continue
                        time.sleep(2)

                self.human_like_actions(driver)

                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "tr.with-hover a.link"))
                    )
                except TimeoutException:
                    if self.is_captcha_page(driver):
                        logger.warning(f"Таймаут из-за капчи на стр. {page_num}")
                        self.handle_captcha(driver, page_url)
                        try:
                            driver.get(page_url)
                        except TimeoutException:
                            logger.warning(f"Страница {page_num} не загрузилась после капчи — скип")
                            page_num += 1
                            continue
                        WebDriverWait(driver, 15).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "tr.with-hover a.link"))
                        )
                    else:
                        logger.info(f"No products found on page {page_num} - stopping")
                        break

                product_elements = driver.find_elements(By.CSS_SELECTOR, "tr.with-hover a.link")
                product_urls = [el.get_attribute('href') for el in product_elements]

                if not product_urls:
                    logger.info(f"No products on page {page_num} - stopping")
                    break

                logger.info(f"Found {len(product_urls)} products on page {page_num}")

                page_components = []
                for i, product_url in enumerate(product_urls):
                    logger.info(f"Parsing product {i + 1}/{len(product_urls)} on page {page_num}")
                    component = self.get_product_features(driver, product_url, category_name)
                    if component:
                        category_components.append(component)
                        page_components.append(component)
                        _products_since_restart += 1

                # Автосохранение после каждой страницы
                if autosave_filepath and page_components:
                    self.save_results_incremental(page_components, autosave_filepath, page_num)

            except Exception as e:
                logger.error(f"Error processing page {page_num}: {e}")

            page_num += 1

        end_label = end_page if end_page else f"{page_num - 1}(auto)"
        logger.info(f"Category completed: {len(category_components)} products from pages {start_page}-{end_label}")
        return category_components, driver

    def save_results(self, components, filename):
        if not components:
            logger.warning("No components to save")
            return

        filepath = os.path.join(BASE_DIR, 'data', 'results', filename)

        data = []
        for comp in components:
            item = {
                'name': comp.name,
                'tu_number': comp.tu_number,
                'manufacturer': comp.manufacturer,
                'supplier': comp.supplier,
                'source': comp.source,
                'article': comp.article,
                'url': comp.url,
                'category': comp.category,
                'voltage': comp.voltage
            }
            # Сливаем базовый словарь с динамическими параметрами
            item.update(comp.all_specs)
            data.append(item)

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"Results saved to {filepath} ({len(data)} records)")
        except Exception as e:
            logger.error(f"Error saving to {filepath}: {e}")

    def _component_to_dict(self, comp):
        """Конвертирует компонент в словарь для сохранения"""
        item = {
            'name': comp.name,
            'tu_number': comp.tu_number,
            'manufacturer': comp.manufacturer,
            'supplier': comp.supplier,
            'source': comp.source,
            'article': comp.article,
            'url': comp.url,
            'category': comp.category,
            'voltage': comp.voltage
        }
        item.update(comp.all_specs)
        return item

    def save_results_incremental(self, new_components, filepath, page_num):
        """Дописывает новые компоненты в файл после каждой страницы.
        Читает существующий файл, объединяет и перезаписывает — безопасно при краше."""
        if not new_components:
            return

        existing_data = []
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
            except Exception as e:
                logger.warning(f"Не удалось прочитать существующий файл: {e}")

        new_data = [self._component_to_dict(c) for c in new_components]
        all_data = existing_data + new_data

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(all_data, f, ensure_ascii=False, indent=2)
            logger.info(f"✓ Автосохранение после стр. {page_num}: +{len(new_data)} товаров "
                        f"(всего в файле: {len(all_data)}) → {filepath}")
        except Exception as e:
            logger.error(f"Ошибка автосохранения: {e}")

    def _load_existing_results(self, filepath):
        """Загружает уже сохранённые результаты и возвращает количество записей"""
        if not os.path.exists(filepath):
            return 0
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return len(data)
        except Exception:
            return 0

    def run(self):
        logger.info("Starting chipdip scraper")
        if PROXY_LIST:
            logger.info(f"Загружено {len(PROXY_LIST)} прокси")
        start_time = time.time()

        # Запрашиваем список категорий
        categories = self.get_categories_from_user()
        if not categories:
            logger.error("Не введено ни одной категории")
            return

        print(f"\n{'='*50}")
        print(f"Категорий к парсингу: {len(categories)}")
        for i, (url, name) in enumerate(categories, 1):
            print(f"  {i}. {name} — {url}")
        print(f"Страницы: ВСЕ (до конца)")
        print(f"{'='*50}\n")

        driver = None

        try:
            for cat_idx, (category_url, category_name) in enumerate(categories, 1):
                print(f"\n{'━'*50}")
                print(f"▶ Категория {cat_idx}/{len(categories)}: {category_name}")
                print(f"{'━'*50}")

                # Новый драйвер для каждой категории — сброс памяти
                if driver is not None:
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    time.sleep(2)
                driver = self.setup_driver()

                # Формируем имя файла для автосохранения
                safe_name = "".join(c for c in category_name if c.isalnum() or c in (' ', '_', '-')).rstrip()
                safe_name = safe_name.replace(' ', '_')
                filename = f"{safe_name}_all_pages.json"
                autosave_filepath = os.path.join(BASE_DIR, 'data', 'results', filename)

                # Проверяем — есть ли уже частично сохранённые данные
                existing_count = self._load_existing_results(autosave_filepath)
                if existing_count > 0:
                    print(f"\n⚠ Найден файл с предыдущими данными: {filename}")
                    print(f"   Уже сохранено записей: {existing_count}")
                    answer = input("Продолжить дозапись? (y/n, Enter=y): ").strip().lower()
                    if answer == 'n':
                        print("Файл будет перезаписан с нуля.")
                        try:
                            os.remove(autosave_filepath)
                        except Exception:
                            pass
                    else:
                        print(f"Продолжаем — новые данные добавятся к {existing_count} записям.")

                logger.info(f"Автосохранение → {autosave_filepath}")

                # Парсим все страницы (end_page=None)
                components, driver = self.parse_category_page_range(
                    driver, category_url, category_name,
                    start_page=1, end_page=None,
                    autosave_filepath=autosave_filepath
                )

                total_in_file = self._load_existing_results(autosave_filepath)
                print(f"✓ Категория «{category_name}» готова: {total_in_file} записей → {filename}")

        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

            total_time = time.time() - start_time
            print(f"\n{'='*50}")
            print(f"✓ Все категории обработаны за {total_time:.1f} сек")
            print(f"{'='*50}")

            # Выводим оставшиеся рабочие прокси
            if PROXY_LIST:
                print(f"\n=== Оставшиеся рабочие прокси ({len(PROXY_LIST)}) ===")
                for p in PROXY_LIST:
                    if p is None:
                        print(f"  [свой IP]")
                    elif p['user']:
                        print(f"  {p['host']}:{p['port']}:{p['user']}:{p['pass']}")
                    else:
                        print(f"  {p['host']}:{p['port']}")
                print("=== Конец списка ===\n")
            else:
                print("\n⚠ Все прокси были забанены!\n")


if __name__ == "__main__":

    scraper = ChipDipScraper()
    scraper.run()