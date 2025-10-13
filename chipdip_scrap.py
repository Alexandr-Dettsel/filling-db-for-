from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
import time
import random
import json
import os
from dataclasses import dataclass
import logging
import argparse

logging.getLogger('selenium').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('selenium.webdriver.remote.remote_connection').setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.chipdip.ru"


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


class ChipDipScraper:
    def __init__(self):
        self.components = []
        self.create_directories()

    def create_directories(self):
        directories = [
            'data',
            'data/results'
        ]
        for directory in directories:
            os.makedirs(directory, exist_ok=True)

    def setup_driver(self):
        options = Options()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-extensions")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--log-level=3")
        options.add_argument("--disable-logging")  # Отключаем логирование
        options.add_experimental_option('excludeSwitches', ['enable-logging'])  # Отключаем логи Chrome

        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        ]
        options.add_argument(f"--user-agent={random.choice(user_agents)}")

        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)

        driver = webdriver.Edge(options=options)
        driver.maximize_window()
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

    def handle_captcha(self, driver, url):
        logger.warning("CAPTCHA detected - manual intervention required")
        input("Solve CAPTCHA in browser and press Enter to continue...")
        driver.get(url)
        self.random_sleep(3, 5)

    def get_product_features(self, driver, url, category):
        try:
            driver.get(url)
            self.random_sleep(3, 6)

            if random.random() > 0.3:
                self.human_like_actions(driver)

            try:
                name_product = driver.find_element(By.TAG_NAME, 'h1')
                product_name = name_product.text
                logger.info(f"Product: {product_name}")
            except NoSuchElementException:
                self.handle_captcha(driver, url)
                return self.get_product_features(driver, url, category)

            try:
                button = driver.find_element(By.CSS_SELECTOR,
                                             'span.link.link_pseudo.link_showhide.f-size.with-icon.with-icon_right.with-icon_sort-down')
                driver.execute_script("arguments[0].click();", button)
                time.sleep(random.uniform(1, 2))
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
                tu_number=params.get('ту', ''),
                manufacturer=params.get('производитель', ''),
                supplier=params.get('поставщик', 'Чип и Дип'),
                article=article,
                url=url,
                category=category
            )

            return component

        except Exception as e:
            logger.error(f"Error parsing product {url}: {e}")
            return None

    def extract_article_from_url(self, url):
        try:
            parts = url.split('/')
            for i, part in enumerate(parts):
                if part == 'product' and i + 1 < len(parts):
                    return parts[i + 1]
        except:
            pass
        return ""

    def parse_category_page(self, driver, category_url, category_name, max_pages=None):
        logger.info(f"Parsing category: {category_name}")

        category_components = []
        page_num = 1
        has_products = True

        while has_products and (max_pages is None or page_num <= max_pages):
            if page_num == 1:
                page_url = category_url
            else:
                base_url = category_url.split('?')[0]
                separator = "?" if "?" not in category_url else "&"
                page_url = f"{base_url}{separator}page={page_num}"

            logger.info(f"Page {page_num} - {page_url}")

            try:
                driver.get(page_url)
                self.random_sleep(3, 6)
                self.human_like_actions(driver)

                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "tr.with-hover a.link"))
                    )
                except TimeoutException:
                    logger.info("No products found - stopping pagination")
                    break

                product_elements = driver.find_elements(By.CSS_SELECTOR, "tr.with-hover a.link")
                product_urls = [el.get_attribute('href') for el in product_elements]

                if not product_urls:
                    logger.info("No products on page - stopping pagination")
                    break

                logger.info(f"Found {len(product_urls)} products")

                for i, product_url in enumerate(product_urls):
                    logger.info(f"Parsing product {i + 1}/{len(product_urls)}")
                    component = self.get_product_features(driver, product_url, category_name)
                    if component:
                        category_components.append(component)

                    if i < len(product_urls) - 1:
                        self.random_sleep(2, 4)

                page_num += 1

                if page_num <= (max_pages if max_pages else 1000):
                    time.sleep(random.uniform(4, 8))

            except Exception as e:
                logger.error(f"Error processing page {page_num}: {e}")
                break

        logger.info(f"Category completed: {len(category_components)} products from {page_num - 1} pages")
        return category_components

    def save_results(self, components, filename):
        if not components:
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
                'category': comp.category
            })

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"Results saved to {filepath} ({len(data)} records)")
        except Exception as e:
            logger.error(f"Error saving to {filepath}: {e}")

    def run(self, max_pages=None):
        logger.info("Starting chipdip scraper")
        start_time = time.time()

        driver = self.setup_driver()

        try:
            catalog_url = f"{BASE_URL}/catalog/electronic-components"
            driver.get(catalog_url)
            time.sleep(3)

            category_elements = driver.find_elements(By.CSS_SELECTOR, "li.catalog__item a.link")
            category_links = [el.get_attribute('href') for el in category_elements]
            category_names = [el.text for el in category_elements]

            if category_links:
                category_url = category_links[0]
                category_name = category_names[0]

                logger.info(f"Processing category: {category_name}")
                components = self.parse_category_page(driver, category_url, category_name, max_pages)

                safe_category_name = "".join(c for c in category_name if c.isalnum() or c in (' ', '_', '-')).rstrip()
                safe_category_name = safe_category_name.replace(' ', '_')
                self.save_results(components, f"category_{safe_category_name}_results.json")

            total_time = time.time() - start_time
            logger.info(f"Scraping completed in {total_time:.1f} seconds")

        finally:
            driver.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='ChipDip Scraper')
    parser.add_argument('--pages', type=int, default=None, help='Max pages to parse per category (default: all pages)')

    args = parser.parse_args()

    scraper = ChipDipScraper()
    scraper.run(max_pages=args.pages)