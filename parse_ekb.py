import pdfplumber
import json
import os
import glob
import re
from collections import defaultdict


def sanitize_filename(name):
    if not name:
        return "Без названия"
    name = re.sub(r'[\\/:*?"<>|]', '_', str(name))
    # Обрезаем жестко до 50 символов чтобы не превысить MAX_PATH (260 символов) в Windows
    return name.strip(' ._\t\n\r')[:50].strip(' ._\t\n\r')


# Глобальные переменные для сохранения маппинга характеристик между разными книгами (PDF-файлами) одного раздела
global_mapping_by_subcat = defaultdict(dict)
global_latest_mapping = {}

def process_pdf(pdf_path, out_dir):
    global global_mapping_by_subcat, global_latest_mapping
    
    data_by_subcat = defaultdict(list)
    current_subcategory = "Общие данные"
    
    import camelot

    try:
        # Получаем общее количество страниц через pdfplumber для отображения прогресса
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            
        print(f"    Анализ файла {os.path.basename(pdf_path)} ({total_pages} стр.)...   ", end='\r', flush=True)

        # Читаем таблицы через camelot. Используем flavor='lattice' для таблиц с линиями
        # или 'stream' для таблиц с отступами. Лучше перебирать страницы по одной
        for page_num in range(1, total_pages + 1):
            print(f"    Анализ страницы {page_num}/{total_pages} файла {os.path.basename(pdf_path)}...   ", end='\r', flush=True)
            
            # Сначала пытаемся найти подраздел и маппинг через текст страницы (отдельно от таблиц)
            with pdfplumber.open(pdf_path) as pdf:
                page = pdf.pages[page_num - 1]
                text = page.extract_text()
                temp_subcat = current_subcategory
                if text:
                    lines = text.split('\n')
                    block_text = ""
                    for line in lines:
                        line_stripped = line.strip()
                        match_sub = re.match(r'^(\d+(?:\.\d+)*)\s+([А-Яа-яЁёA-Za-z].*)$', line_stripped)
                        if match_sub and "±" not in line_stripped and "ТУ" not in line_stripped:
                            # Обработка накопленного текста предыдущего раздела
                            if block_text.strip():
                                flat = "  " + block_text
                                for m in re.finditer(r'\s(\d+)\.\s+([^\d\s][^;]+)(?=\s\d+\.\s+[^\d\s]|;|$)', flat):
                                    key = str(m.group(1))
                                    val = m.group(2).strip().rstrip(';')
                                    val = re.split(r'\s{2,}(?=\d+\s+|Раздел|Перечень|Таблица)', val)[0] 
                                    val = re.sub(r'\s+', ' ', val).strip()
                                    if len(val) < 200 and "ТУ" not in val and "Раздел" not in val and not val.lower().startswith(("основанием", "применение", "в целях", "по запросам", "изделия", "приложение", "исключить", "пояснительная")):
                                        global_mapping_by_subcat[temp_subcat][key] = val
                            
                            sub_num = match_sub.group(1)
                            sub_text = match_sub.group(2)
                            if not str(sub_text).startswith(("В ", "мА", "мВ", "Ом", "кВт", "%", "Гц", "МГц")):
                                temp_subcat = sanitize_filename(f"{sub_num} {sub_text}")
                            block_text = line_stripped
                        else:
                            block_text += "  " + line_stripped
                            
                    # Обработка оставшегося текста в самом конце страницы
                    if block_text.strip():
                        flat = "  " + block_text
                        for m in re.finditer(r'\s(\d+)\.\s+([^\d\s][^;]+)(?=\s\d+\.\s+[^\d\s]|;|$)', flat):
                            key = str(m.group(1))
                            val = m.group(2).strip().rstrip(';')
                            val = re.split(r'\s{2,}(?=\d+\s+|Раздел|Перечень|Таблица)', val)[0] 
                            val = re.sub(r'\s+', ' ', val).strip()
                            if len(val) < 200 and "ТУ" not in val and "Раздел" not in val and not val.lower().startswith(("основанием", "применение", "в целях", "по запросам", "изделия", "приложение", "исключить", "пояснительная")):
                                global_mapping_by_subcat[temp_subcat][key] = val
            
            # Теперь используем Camelot для извлечения таблиц
            try:
                tables = camelot.read_pdf(pdf_path, pages=str(page_num), flavor='stream', edge_tol=500)
            except Exception:
                tables = []
                
            for table in tables:
                df = table.df
                if df.empty or len(df.columns) < 3:
                    continue
                    
                # Ищем индекс строки, где начинаются реальные данные (по наличию "ТУ" или явных компонентов)
                data_start_idx = 0
                for idx, row in df.iterrows():
                    row_str = " ".join(str(c) for c in row.values)
                    # Если находим "ТУ" и это не заголовок таблицы
                    if "ТУ" in row_str and "обозначение" not in row_str.lower():
                        data_start_idx = idx
                        break
                        
                if data_start_idx == 0:
                    continue
                    
                # 1. Собираем правильные заголовки таблицы из всех строк ДО данных
                headers = []
                for r_idx in range(data_start_idx):
                    row_parts = [str(c).replace('\n', ' ').strip() for c in df.iloc[r_idx].values]
                    row_str = " ".join(c for c in row_parts if c)
                    row_str = re.sub(r'\s+', ' ', row_str).strip()
                    
                    # Если подраздел написан прямо в начале таблицы (до данных)
                    match_sub = re.match(r'^(\d+(?:\.\d+)+)\s+([А-Яа-яЁё].*?)$', row_str) 
                    if match_sub and "ТУ" not in row_str and "Раздел" not in row_str:
                        sub_text = match_sub.group(2)
                        if not any(x in sub_text for x in ["мА", "мВ", "Ом", "кВт", "%", "Гц", "МГц"]):
                            current_subcategory = sanitize_filename(f"{match_sub.group(1)} {sub_text}")

                if global_mapping_by_subcat[current_subcategory]:
                    global_latest_mapping.update(global_mapping_by_subcat[current_subcategory])
                mapping = global_latest_mapping
                
                # Запасной сбор маппинга из шапки с правильным проходом по строкам!
                header_text_block = "  ".join(str(df.iloc[r, c]).replace('\n', '  ') for r in range(data_start_idx) for c in range(len(df.columns)))
                for m in re.finditer(r'\s(\d+)\.\s+([^\d\s][^;]+)(?=\s\d+\.\s+[^\d\s]|;|$)', "  " + header_text_block):
                    key = str(m.group(1))
                    val = m.group(2).strip().rstrip(';')
                    val = re.split(r'\s{2,}(?=\d+\s+|Раздел|Перечень)', val)[0]
                    val = re.sub(r'\s+', ' ', val).strip()
                    if len(val) < 200 and "ТУ" not in val and not val.lower().startswith(("основанием", "применение", "в целях", "по запросам", "изделия", "приложение", "исключить", "пояснительная")):
                        mapping[key] = val
                        global_mapping_by_subcat[current_subcategory][key] = val

                for col_idx in range(len(df.columns)):
                    h_parts = []
                    for r_idx in range(data_start_idx):
                        val = str(df.iloc[r_idx, col_idx]).replace('\n', ' ').strip()
                        # Убираем дубликаты слов-заголовков и мусор от колонтитулов
                        if val and "Перечень ЭКБ" not in val and not re.match(r'^Раздел\s+\d', val) and "Основные технические" not in val:
                            if not val.lower().startswith(("основанием", "применение", "в целях", "по запросам", "изделия", "приложение", "исключить", "пояснительная", "рассылка", "примечание")):
                                if val not in h_parts:
                                    h_parts.append(val)
                    header_name = " ".join(h_parts).strip()
                    
                    # Подготовка для поиска базовых  столбцов (убираем дефисы, пробелы)
                    lower_hn = header_name.replace('-', '').replace(' ', '').lower()
                    
                    if ("условноеобозначение" in lower_hn or "обозначениеизделия" in lower_hn or ("обозначение" in lower_hn and "документ" not in lower_hn)) and "корпус" not in lower_hn and not re.match(r'^\d', header_name.strip()):
                        header_name = "Условное обозначение"
                    elif "документ" in lower_hn or "поставку" in lower_hn or "ту" in lower_hn:
                        header_name = "Обозначение документа на поставку"
                    elif ("отли" in lower_hn or "читель" in lower_hn) and "знак" in lower_hn:
                        header_name = "Отличительный знак"
                    elif "изготовитель" in lower_hn or "предприятие" in lower_hn or "калько" in lower_hn:
                        header_name = "Предприятие-изготовитель"
                    elif "номер" in lower_hn or "позиц" in lower_hn or "пози" in lower_hn:
                        header_name = "Номер позиции"
                    elif header_name:
                        # Ищем ПЕРВУЮ одиночную цифру в заголовке, это дает нам 100% гарантию номера колонки
                        digits = re.findall(r'\b(\d)\b', header_name)
                        
                        if "1." in header_name and "2." in header_name and "3." in header_name:
                            header_name = "Слипшиеся_характеристики"
                        elif len(digits) > 1:
                            header_name = "Слипшиеся_характеристики_" + "_".join(digits)
                        elif digits:
                            header_name = f"MAP_{digits[0]}"
                        else:
                            # Пытаемся найти хотя бы кусок текста из начального маппинга
                            matched = False
                            for k, v in mapping.items():
                                if len(header_name) > 5 and (header_name.lower() in v.lower() or v.lower() in header_name.lower()):
                                    header_name = f"MAP_{k}"
                                    matched = True
                                    break
                            if not matched:
                                header_name = re.sub(r'(?:\b\d\b\s+){2,}', '', header_name).strip()
                                header_name = re.sub(r'\s+', ' ', header_name).strip()
                                
                    if not header_name:
                        # Fallbacks for exact column indices if header is completely empty (e.g. table continued on next page)
                        if col_idx == 0: header_name = "Номер позиции"
                        elif col_idx == 1: header_name = "Условное обозначение"
                        elif col_idx == 2: header_name = "Обозначение документа на поставку"

                    headers.append(header_name if header_name else f"Колонка_{col_idx+1}")
                    
                raw_headers = headers.copy()
                
                # Построение итоговых заголовков (решает проблему дубликатов и безымянных колонок)
                def build_final_headers(r_headers, current_map):
                    n_headers = []
                    u_h = set()
                    m_vals = list(current_map.values()) if current_map else []
                    
                    # Пытаемся прибить базовые колонки гвоздями, если в них попала хрень
                    for idx, h in enumerate(r_headers):
                        a_h = h
                        if idx == 0 and "номер" not in a_h.lower(): a_h = "Номер позиции"
                        if idx == 1 and "условное" not in a_h.lower(): a_h = "Условное обозначение"
                        if idx == 2 and "документ" not in a_h.lower(): a_h = "Обозначение документа на поставку"
                        
                        r_headers[idx] = a_h

                    for h in r_headers:
                        a_h = h
                        if a_h.startswith("MAP_"):
                            k = a_h.split('_')[1]
                            a_h = current_map.get(k, k)
                            
                        if a_h.startswith("Слипшиеся_характеристики"):
                            if "Слипшиеся_характеристики_" in h:
                                keys = h.split("Слипшиеся_характеристики_")[1].split("_")
                                for k in keys:
                                    u_h.add(current_map.get(k, k))
                            else:
                                for mk in m_vals:
                                    u_h.add(mk)
                            n_headers.append(a_h)
                            continue
                            
                        if a_h in u_h or a_h.startswith("Колонка_"):
                            if m_vals:
                                free_key = next((v for v in m_vals if v not in u_h), None)
                                if free_key:
                                    a_h = free_key
                                    
                        if a_h:
                            u_h.add(a_h)
                        n_headers.append(a_h)
                    return n_headers

                headers = build_final_headers(raw_headers, mapping)
                
                # 2. Обрабатываем строки с данными
                for idx in range(data_start_idx, len(df)):
                    row = df.iloc[idx]
                    cleaned_row = [str(cell).replace('\n', ' ').strip() for cell in row.values]
                    row_str = " ".join(c for c in cleaned_row if c)
                    row_str = re.sub(r'\s+', ' ', row_str).strip()
                    
                    if not row_str:
                        continue
                        
                    # Определяем, является ли строка новой записью (обычно заполнены первые колонки: Условное обозначение или Номер позиции)
                    is_new_item = bool(cleaned_row[0] or (len(cleaned_row) > 1 and cleaned_row[1]))
                        
                    # Смена категории прямо внутри таблицы
                    match_sub = re.match(r'^(\d+(?:\.\d+)+)\s+([А-Яа-яЁё].*?)$', row_str) 
                    if match_sub and "ТУ" not in row_str and "Раздел" not in row_str:
                        sub_text = match_sub.group(2)
                        if not any(x in sub_text for x in ["мА", "мВ", "Ом", "кВт", "%", "Гц", "МГц"]):
                            current_subcategory = sanitize_filename(f"{match_sub.group(1)} {sub_text}")
                            
                            if global_mapping_by_subcat[current_subcategory]:
                                global_latest_mapping.update(global_mapping_by_subcat[current_subcategory])
                            mapping = global_latest_mapping
                            headers = build_final_headers(raw_headers, mapping)
                            continue
                            
                    # Обновляем маппинг на случай появления новых характеристик
                    if global_mapping_by_subcat[current_subcategory]:
                        global_latest_mapping.update(global_mapping_by_subcat[current_subcategory])
                    mapping = global_latest_mapping

                    # Проверка не является ли это строкой маппинга
                    has_mapping = False
                    for m in re.finditer(r'(?:^|\s)(\d+)\.\s+([А-ЯA-Z].+?)(?=(?:;|\s\d+\.\s+[А-ЯA-Z]|$))', row_str):
                        key = str(m.group(1))
                        val = m.group(2).strip().rstrip(';')
                        val = re.split(r'\s{2,}|\t', val)[0]
                        if len(val) < 200 and "ТУ" not in val and not val.lower().startswith(("основанием", "применение", "в целях", "по запросам", "изделия", "приложение", "исключить", "пояснительная")):
                            global_mapping_by_subcat[current_subcategory][key] = val
                            has_mapping = True
                    
                    if has_mapping:
                        global_latest_mapping.update(global_mapping_by_subcat[current_subcategory])
                        mapping = global_latest_mapping
                        headers = build_final_headers(raw_headers, mapping)
                        if not is_new_item:
                            continue
                    
                    if is_new_item:
                        item_data = {}
                        for h_name, c in zip(headers, cleaned_row):
                            if not c:
                                continue
                                
                            actual_h = h_name
                                
                            # Стираем куски текста типа "2.3 Оптопары тиристорные", которые Camelot впихнул в название детали
                            if actual_h == "Условное обозначение" and re.search(r'\s+\d+(?:\.\d+)+\s+[А-Я]', c):
                                c = re.sub(r'\s+\d+(?:\.\d+)+\s+[А-Я].*', '', c).strip()
                                
                            # Стираем куски описаний характеристик, которые Camelot случайно съел в значения, например "150 1. Входное напряжение..."
                            if re.search(r'\s*\d+\.\s+[А-ЯA-Z]', c):
                                c = re.sub(r'\s*\d+\.\s+[А-ЯA-Z].*', '', c).strip()
                                if not c:
                                    continue
                            
                            # Если Camelot слил колонки в одну ячейку, а мы знаем маппинг
                            if actual_h.startswith("Слипшиеся_характеристики") and mapping:
                                parts = re.split(r'\s{2,}', c.strip())
                                
                                map_keys = []
                                if "Слипшиеся_характеристики_" in actual_h:
                                    keys = actual_h.split("Слипшиеся_характеристики_")[1].split("_")
                                    map_keys = [mapping.get(k, k) for k in keys]
                                else:
                                    map_keys = list(mapping.values())

                                if len(parts) == len(map_keys):
                                    for k_name, p_val in zip(map_keys, parts):
                                        item_data[k_name] = p_val
                                else:
                                    # Если не совпадает, просто бьем на сколько получится
                                    for i, p_val in enumerate(parts):
                                        k_name = map_keys[i] if i < len(map_keys) else f"Доп_{i+1}"
                                        item_data[k_name] = p_val
                            else:
                                item_data[actual_h] = c
                        data_by_subcat[current_subcategory].append(item_data)
                    else:
                        # Продолжение характеристик предыдущей детали
                        if data_by_subcat[current_subcategory]:
                            last_item = data_by_subcat[current_subcategory][-1]
                            for h_name, c in zip(headers, cleaned_row):
                                if c:
                                    actual_h = h_name

                                    # Если сюда тоже попал мусор, чистим его
                                    if re.search(r'\s*\d+\.\s+[А-ЯA-Z]', c):
                                        c = re.sub(r'\s*\d+\.\s+[А-ЯA-Z].*', '', c).strip()
                                        if not c:
                                            continue
                                            
                                    # Эвристика: если это температура и она попала в левые/другие колонки, перенаправляем в Рабочую температуру
                                    if re.search(r'-?\d+\s*÷\s*[+-]?\d+', c) and "температура" not in actual_h.lower():
                                        temp_key = next((k for k in last_item.keys() if "температур" in k.lower()), None)
                                        if not temp_key:
                                            temp_key = next((h for h in headers if "температур" in h.lower()), "Рабочая температура, °С")
                                        if temp_key in last_item:
                                            last_item[temp_key] += " " + c
                                        else:
                                            last_item[temp_key] = c
                                        continue

                                    if actual_h.startswith("Слипшиеся_характеристики") and mapping:
                                        parts = re.split(r'\s{2,}', c.strip())
                                        
                                        map_keys = []
                                        if "Слипшиеся_характеристики_" in actual_h:
                                            keys = actual_h.split("Слипшиеся_характеристики_")[1].split("_")
                                            map_keys = [mapping.get(k, k) for k in keys]
                                        else:
                                            map_keys = list(mapping.values())
                                            
                                        for i, p_val in enumerate(parts):
                                            k_name = map_keys[i] if i < len(map_keys) else f"Доп_{i+1}"
                                            if k_name in last_item:
                                                last_item[k_name] += " " + p_val
                                            else:
                                                last_item[k_name] = p_val
                                    elif actual_h == "Слипшиеся_характеристики":
                                        actual_h = "Нераспарсенные данные"
                                        if actual_h in last_item:
                                            last_item[actual_h] += " " + c
                                        else:
                                            last_item[actual_h] = c
                                    else:
                                        if actual_h in last_item:
                                            last_item[actual_h] += " " + c
                                        else:
                                            last_item[actual_h] = c

    except Exception as e:
        print(f"\n[Ошибка чтения] {os.path.basename(pdf_path)}: {e}")
        return

    print() # Переход на новую строку после завершения обработки страниц

    # Сохраняем каждый найденный подраздел в отдельный JSON
    if not data_by_subcat:
        print(f"  -> В файле {os.path.basename(pdf_path)} не найдено нужных данных.")
        return

    for subcat, rows in data_by_subcat.items():
        if subcat == "Общие данные" and not rows:
            continue

        subcat_name = sanitize_filename(subcat)
        if not subcat_name:
            subcat_name = "Без подраздела"

        out_file = os.path.join(out_dir, f"{subcat_name}.json")
        out_file = os.path.abspath(out_file)
        if os.name == 'nt' and not out_file.startswith('\\\\?\\'):
            out_file = '\\\\?\\' + out_file

        # Если файл уже существует от другой книги, объединяем данные
        if os.path.exists(out_file):
            try:
                with open(out_file, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
                if isinstance(existing, list):
                    # Отфильтруем дубликаты по условному обозначению, отдавая приоритет новым (только что спарсенным) данным
                    new_names = {r.get("Условное обозначение") for r in rows if r.get("Условное обозначение")}
                    filtered_existing = [e for e in existing if e.get("Условное обозначение") not in new_names and e.get("Условное обозначение")]
                    rows = filtered_existing + rows
            except Exception:
                pass

        with open(out_file, 'w', encoding='utf-8') as f:
            json.dump(rows, f, ensure_ascii=False, indent=4)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.join(script_dir, "ЭКБ_2019")
    output_base_dir = os.path.join(script_dir, "parsed_ekb_data_json")

    pdf_files = glob.glob(os.path.join(base_dir, "**", "*.pdf"), recursive=True)
    
    # Обязательно сортируем файлы, чтобы Книга 1 шла перед Книга 2 и скрипт
    # запоминал характеристики (маппинг) в правильном хронологическом порядке
    pdf_files.sort()

    print(f"Найдено PDF файлов: {len(pdf_files)}")

    for pdf_file in pdf_files:
        # Пропускаем файл изменений
        if "Изм" in os.path.basename(pdf_file) or "Изменения" in os.path.basename(pdf_file):
            continue

        # === ВОССОЗДАЕМ ИЕРАРХИЮ ПАПОК КАК В ОРИГИНАЛЕ ===
        # Получаем относительный путь от папки ЭКБ_2019
        rel_path = os.path.relpath(pdf_file, base_dir)
        # Получаем папку (например: "01 - Изделия СВЧ")
        rel_dir = os.path.dirname(rel_path)

        clean_parts = [p.strip(' ._\t\n\r') for p in rel_dir.split(os.sep)]
        rel_dir = os.sep.join(clean_parts)

        # Директория для сохранения - это папка самого раздела
        out_dir = os.path.abspath(os.path.join(output_base_dir, rel_dir))
        if os.name == 'nt' and not out_dir.startswith('\\\\?\\'):
            out_dir = '\\\\?\\' + out_dir
        os.makedirs(out_dir, exist_ok=True)

        print(f"Обработка: {rel_path} -> {out_dir}")
        process_pdf(pdf_file, out_dir)


if __name__ == "__main__":
    main()
