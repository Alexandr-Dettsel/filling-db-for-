import os
import glob

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_base_dir = os.path.join(script_dir, "parsed_ekb_data_json")
    
    # Ищем все json файлы во всех подпапках
    json_files = glob.glob(os.path.join(output_base_dir, "**", "*.json"), recursive=True)
    
    total_braces = 0
    file_count = 0
    
    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                count = content.count('{')
                total_braces += count
                file_count += 1
        except Exception as e:
            print(f"Ошибка при чтении {file_path}: {e}")
            
    print(f"Обраработано файлов JSON: {file_count}")
    print(f"Общее количество символов '{{' во всех файлах: {total_braces}")
    
    # Так как каждый объект (деталь) в JSON оборачивается в {}, 
    # это количество примерно равно общему числу спарсенных деталей.

if __name__ == "__main__":
    main()

