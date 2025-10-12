import io
import os

from PIL import Image
from selenium.webdriver.support import expected_conditions as EC
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException
from twocaptcha import TwoCaptcha

import time

from selenium.webdriver.support.wait import WebDriverWait

BASE_URL = "https://www.chipdip.ru"


def solver_simple_captcha(driver):
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

        return True

    except Exception as e:
        print(f"Ошибка: {e}")
        driver.switch_to.default_content()
        return False


def get_product_features(driver, url):
    driver.get(url)

    try:
        name_product = driver.find_element(By.TAG_NAME, 'h1')
        print(f"\nТовар: {name_product.text}")
    except NoSuchElementException:
        while True:
            print("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA CAPCHA AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
            solver_simple_captcha(driver)
            time.sleep(20)
            try:
                if driver.find_element(By.TAG_NAME, 'h1') is not None:
                    break
            except NoSuchElementException:
                pass

        get_product_features(driver, url)


    try:
        button = driver.find_element(By.CSS_SELECTOR,
                                     'span.link.link_pseudo.link_showhide.f-size.with-icon.with-icon_right.with-icon_sort-down')
        driver.execute_script("""
                    var event = new MouseEvent('click', {
                        view: window,
                        bubbles: true,
                        cancelable: true
                    });
                    arguments[0].dispatchEvent(event);
                """, button)

    except NoSuchElementException:
        pass

    names = driver.find_elements(By.CLASS_NAME, 'product__param-name')
    values = driver.find_elements(By.CLASS_NAME, 'product__param-value')

    for name, value in zip(names, values):
        print(f"{name.text}: {value.text}")

def parsing_page(driver, url):
    driver.get(url)

    while True:
        product_elements = driver.find_elements(By.CSS_SELECTOR, "tr.with-hover a.link")
        product_urls = [el.get_attribute('href') for el in product_elements]

        for href in product_urls:
            get_product_features(driver, href)

        try:
            next_button = driver.find_element(By.CSS_SELECTOR, "a.link.pager__page.pager__control.pager__next")
            next_href = next_button.get_attribute('href')
            driver.get(next_href)
        except NoSuchElementException:
            print("Достигнута последняя страница каталога.")
            break

def main():
    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    driver = webdriver.Edge(options=options)
    driver.maximize_window()

    catalog_url = f"{BASE_URL}/catalog/electronic-components"
    driver.get(catalog_url)

    category_elements = driver.find_elements(By.CSS_SELECTOR, "li.catalog__item a.link")
    category_links = [el.get_attribute('href') for el in category_elements]

    for category_url in category_links:
        print(f"\nПарсинг категории: {category_url}")
        parsing_page(driver, category_url)

    driver.quit()

if __name__ == "__main__":
    main()
