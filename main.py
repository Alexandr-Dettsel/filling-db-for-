import base64
import os

from selenium import webdriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from twocaptcha import TwoCaptcha

import time

BASE_URL = "https://www.chipdip.ru"
API_KEY = "f057f71085e510b890ddd261af4bbedf"


def solver_simple_captcha(driver):
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
    print('checkbox отмечен')


def get_sitekey_from_iframe(driver):
    try:
        iframe = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "iframe[data-testid='advanced-iframe']"))
        )
        iframe_src = iframe.get_attribute('src')

        if 'sitekey=' in iframe_src:
            sitekey = iframe_src.split('sitekey=')[1].split('&')[0]
            print(f"✓ Найден sitekey: {sitekey}")
            return sitekey
        else:
            print("✗ Нет sitekey в iframe")
            return None

    except Exception as e:
        print(f"Ошибка получения sitekey: {e}")
        return None


def get_captcha_images(driver):
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

        print("Получены изображения капчи")
        return main_image, instruction_image, img_element

    except Exception as e:
        print(f"Ошибка получения изображений: {e}")
        driver.switch_to.default_content()
        return None, None, None


def solver_difficult_captcha(driver):
    solver = TwoCaptcha(API_KEY)

    try:
        main_img, instruction_img, img_element = get_captcha_images(driver)

        if not main_img or not instruction_img:
            print("Не получили изображения")
            return False

        print("Отправка в 2captcha")

        result = solver.coordinates(
            file=f"data:image/png;base64,{main_img}",
            hintImg=f"data:image/png;base64,{instruction_img}"
        )

        coordinates = parse_coordinates(result['code'])
        click_silhouettes(driver, coordinates)

        time.sleep(1)

        iframe = driver.find_element(By.CSS_SELECTOR, "iframe[data-testid='advanced-iframe']")
        driver.switch_to.frame(iframe)

        submit_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-testid='submit']"))
        )
        submit_btn.click()
        print("Submit нажат")

        driver.switch_to.default_content()
        time.sleep(1)
        return True
    except Exception as e:
        print(f"Ошибка решения капчи: {e}")
        driver.switch_to.default_content()
        return False


def parse_coordinates(coords_string):
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


def click_silhouettes(driver, coordinates):
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
        print("✓ Все клики выполнены")
        return True

    except Exception as e:
        print(f"Ошибка кликов: {e}")
        driver.switch_to.default_content()
        return False


def get_product_features(driver, url):
    driver.get(url)

    try:
        name_product = driver.find_element(By.TAG_NAME, 'h1')
        print(f"\nТовар: {name_product.text}")
    except NoSuchElementException:
        attempt = 0
        max_attempts = 10

        while attempt < max_attempts:
            attempt += 1

            try:
                driver.switch_to.default_content()
            except:
                pass

            if solver_simple_captcha(driver):
                driver.switch_to.default_content()
                time.sleep(5)

                try:
                    name_product = driver.find_element(By.TAG_NAME, 'h1')
                    print(f"Товар: {name_product.text}")
                    break
                except NoSuchElementException:
                    pass

            print("→ Решаем сложную капчу...")
            if solver_difficult_captcha(driver):
                driver.switch_to.default_content()
                time.sleep(10)

                try:
                    name_product = driver.find_element(By.TAG_NAME, 'h1')
                    print(f"Товар: {name_product.text}")
                    break
                except NoSuchElementException:
                    print(f"✗ Попытка {attempt} провалена")
                    driver.refresh()
                    time.sleep(3)
            else:
                print(f"✗ Попытка {attempt} не удалась")
                time.sleep(5)

        driver.switch_to.default_content()
        try:
            name_product = driver.find_element(By.TAG_NAME, 'h1')
            print(f"Товар: {name_product.text}")
        except NoSuchElementException:
            print("не решили капчу, пробуем ещё")
            return get_product_features(driver, url)

    driver.switch_to.default_content()

    try:
        button = driver.find_element(By.CSS_SELECTOR,
                                     'span.link.link_pseudo.link_showhide.f-size.with-icon.with-icon_right.with-icon_sort-down')
        driver.execute_script("arguments[0].click();", button)
        time.sleep(1)
    except NoSuchElementException:
        pass

    try:
        names = driver.find_elements(By.CLASS_NAME, 'product__param-name')
        values = driver.find_elements(By.CLASS_NAME, 'product__param-value')

        for name, value in zip(names, values):
            print(f"{name.text}: {value.text}")
    except Exception as e:
        print(f"Ошибка парсинга: {e}")


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
