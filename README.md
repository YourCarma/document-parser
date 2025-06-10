# Sova Parser

## Overwiew
Сова-парсер - проект, предназначенный для парсинга различных типов файлов, извлечение их элементов и конвертации их в другие форматы с сохранением форматирования, а так же [перевода](https://py-googletrans.readthedocs.io/en/latest/) ,[озвучивания](https://huggingface.co/coqui/XTTS-v2) и [краткой выжимки](http://192.168.0.67:8009/docs#/LLM_Tools/llm_tools_summary_llm_tools_summary_text_post) на основе библиотеки [docling](https://github.com/docling-project/docling) при помощи движка [EasyOCR](https://www.jaided.ai/easyocr/documentation/). Процесс парсинга документов транслируется по веб-сокетам, клиент может опционально выбрать изображения и таблицы для извлечения, перевести и прослушать текст.    

## Features 
### Поддерживыемые форматы файлов:
```python
ALLOWED_MIME_TYPES: List[str] = [
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/doc",
    "application/msword",
    "application/pdf",
    "image/png",    
    "image/jpeg",
    "image/webp",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint",
]
```

### Поддерживаемые языки:
```python 
ALLOWED_LANGS: List[str] = ["ru","en","ar","fr","uk"]
```
 - `ru` - Русский
 - `en` - Английский (default)
 - `ar` - Арабский
 - `fr` - Французский
 - `uk` - Украинский
  
### Поддерживаемые форматы извлечения:
```python 
ALLOWED_CONVERTED_TYPES: List[str] = ["md","json","yaml","txt"]
```
 - `txt` - .txt
 - `json` - .json
 - `yaml` - .yaml
 - `md` - .md
 
### Дополнительные элементы
- 📊 Таблицы
- 🖼️ Картинки

### Дополнительные опции
- Озвучивание
- Перевод

## Remark
 - `FastAPI` не позволяет в `Swagger` документировать сокеты, поэтому оставлю здесь руководство по ручке `ws://ip:port/parser/extract`: 
    ### Входные данные:
     - **path** `[str]` - пути к документам, которые необходимо обработать 
     - **src_lang** `str` - возможные [языки документов](#поддерживаемые-языки) (`en` - англ. предустановлен заранее)
     - **target_conv_format** `str` - желаемый [формат извлечения](#поддерживаемые-форматы-извлечения)
     - **extracted_elements** `[str]` - [дополнительные элементы](#дополнительные-элементы)
     - **translated** `bool` - флаг для осуществления перевода
     - **target_lang** `str` - целевой язык перевода 
     - **max_num_page** `int` - номер страницы в документе, ПО КОТОРУЮ провести парсинг 
     ```python
        PAGE_RANGE = (1,max_num_page) # -> с 1-й по n-ую
     ```    
     - **prefer_description** ``bool`` - только для изображений   
        - `True` - исследовать само изображение и описать его 
        - `False` - извлечь текст с изображения 
    
    ### Выходные данные:
    ```json
        [
            "filename":{
                "text":"Извлеченный текст",
                "summary":"Сокращение извлеченного текста",
                "images":["путь к изображениям"],
                "tables":["путь к таблицам"],
            }
        ]
    ```
 - В директории `/documents` лежат исходники тестовых документов.
    Результаты структурированно записываются в директорию `/scratch`, где создается новая директория согласно имени исходного документа и времени выполнения конвертации, содержит в себе отдельно результаты на определенном языке и в выбранном формате, результат перевода и элементы (картинки - `pictures`, таблицы - `tables`) выбранные для извлечения опционально.
 - Процесс работы с каждым файлом отслеживается в `Webhook Manager Service`

## Quick start

1.  Сборка образа определенной версии

    ```shell
    docker build -t sova-parser:version .
    ```

2) Запуск контейнера  

    ```shell
    docker run -d \
        --name sova-parser \
        -p 1337:1337 \
        --restart unless-stopped \
        sova-parser:version 
    ```
3) Просмотр логов

    ```shell
    docker logs -f sova-parser
    ```