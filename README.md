# Sova Parser

## Overwiew
Сова-парсер - сервисы\, предназначенный для обработки различных типов файлов, извлечение их элементов и конвертации их в [markdown](https://en.wikipedia.org/wiki/Markdown) с сохранением форматирования, а так же `перевода` на основе библиотеки [docling](https://github.com/docling-project/docling) при помощи движка [EasyOCR](https://www.jaided.ai/easyocr/documentation/). Процесс работы с документами отслеживается в веб-хук менеджере, пользователь может опционально выбрать изображения и таблицы для извлечения, а также перевести текст.    

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