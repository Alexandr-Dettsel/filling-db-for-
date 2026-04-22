import os
import sys

sys.path.insert(0, os.path.abspath('../../' if os.path.basename(os.path.abspath('.')) == 'source' else '../'))

# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'ChipDip Scraper'
copyright = '2026, Dettsel'
author = 'Dettsel'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

# Позволит собирать документацию, не устанавливая зависимости самого парсера
autodoc_mock_imports = [
    "twocaptcha",
    "selenium",
    "undetected_chromedriver",
    "colorama",
    "urllib3",
    "bs4",
    "requests",
    "webdriver_manager",
    "dotenv"
]

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon', # Для поддержки стилей Google/NumPy
]

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

language = 'ru'

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'alabaster'
html_static_path = ['_static']
