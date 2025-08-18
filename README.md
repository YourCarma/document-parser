# Sova Parser

## Overwiew
Сова-парсер - сервис, предназначенный для обработки различных типов файлов, извлечение их элементов и конвертации их в [markdown](https://en.wikipedia.org/wiki/Markdown) с сохранением форматирования, а так же `перевода` на основе библиотеки [docling](https://github.com/docling-project/docling) при помощи движка [EasyOCR](https://www.jaided.ai/easyocr/documentation). Доступен перевод извлеченного текста.    

## API 
`/parser/parse [POST]`
##### Входные данные: 
 - **files** `files[]` - выбранные документы документы
 - **translated** `str` - True/False - необходимость перевода
 - **src_lang** `str` - исходный язык
 - **target_lang** `str` - целевой язык
 - **max_num_page** `str` - предельное кол-во исследуемых страниц
##### Выходные данные (share-линки с обработанным документам в S3 MiniO):
 - ```python
    class ParseFileResult(BaseModel):
        original_file_share_link:str 
        parse_file_share_link:str
        translated_file_share_link:str

    class ParseResponse(BaseModel):
        results: list[ParseFileResult]
    ```

## Features 
### Поддерживыемые форматы файлов:
```python
ALLOWED_MIME_TYPES: List[str] = [
    "image/jpeg",
    "image/webp",
    "image/png",
    "image/tiff",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.oasis.opendocument.text",
    "application/pdf",
    "text/html",
]
```
 - `jpeg`,`jpg`,`webp` - Изображения
 - `doc`,`docx`,`odt` - Текстовые документы
 - `pdf`,`html` - Другие форматы


### Поддерживаемые языки:
```python 
ALLOWED_LANGS: List[str] = ["ru","en","ar","fr","uk"] # Согласно стандарту iso-639
```
 - `ru` - Русский
 - `en` - Английский (default)
 - `ar` - Арабский
 - `fr` - Французский
 - `uk` - Украинский
  
### Дополнительные элементы
- 📊 Таблицы
- 🖼️ Картинки

### Дополнительные опции
- Перевод


### Remark
 - В директории `/documents` лежат файлы-пробники для наглядной работы сервиса.


## Quick start

1.  Сборка образа определенной версии
    ```shell
    make build VERSION=[version]
    ```

2) Запуск контейнера  
    ```shell
    make run VERSION=[version]
    ```

3) Просмотр логов

    ```shell
    make logs
    ```