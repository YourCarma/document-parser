# Перевод документа с фронтенда: task gateway → document-parser → webhook manager

Инструкция для фронтенда. Описывает весь путь: положить файл в хранилище,
поставить задачу, следить за прогрессом, забрать результат, отменить.
Больше ничего читать не нужно; актуальный контракт всегда можно перечитать у
самого сервиса: `GET /api/v1/contract.md` у document-parser.

## 0. Адреса и общие правила

| Сервис | Адрес (dev) | Зачем фронтенду |
| --- | --- | --- |
| task gateway | `http://dev.sova.local:10020` | поставить задачу, отменить задачу |
| webhook manager | `http://dev.sova.local:10001` | прогресс и результат задачи |
| watchtower | уточнить у платформенной команды (в деплое порт `2893`) | загрузить исходный файл, скачать результат |
| resource manager | уточнить у платформенной команды (в деплое порт `2899`) | узнать бакет пользователя |
| document-parser | напрямую не нужен | работает из очереди, фронтенд с ним не общается |

Оба сервиса, с которыми фронтенд ходит напрямую, отдают permissive CORS: запросы
из браузера проходят без прокси. Swagger: `http://dev.sova.local:10020/docs` и
`http://dev.sova.local:10001/docs`.

Правила, которые действуют везде:

- **Пользователь передаётся заголовком `x-user-id`** (у webhook manager он
  пишется `X-User-ID`, регистр не важен). Значение — строка, одна и та же во
  всех запросах: по ней определяется бакет и собирается ключ задачи.
- **Ключ задачи `task_key`** имеет вид `{user_id}:document-parser:{task_uuid}`.
  Его возвращает гейтвей при постановке. Сохраните его: по нему задача
  находится в списке задач пользователя и делается отмена.
- **Фронтенд никогда не ходит в RabbitMQ** и не обновляет задачу в webhook
  manager сам. Только ставит, читает и отменяет.

Схема:

```
фронтенд ──PUT file/upload──▶ watchtower (бакет пользователя)
фронтенд ──POST /api/v1/broker/publish──▶ task gateway ──▶ очередь ──▶ document-parser
фронтенд ◀──GET /api/v1/storage/tasks (опрос)── webhook manager ◀── прогресс и результат ──┘
фронтенд ──POST /api/v1/tasks/cancel──▶ task gateway ──▶ webhook manager (CANCELLED)
```

## 1. Подготовка: файл должен лежать в бакете пользователя

document-parser не принимает файл в задаче. Он получает **object key** файла в
персональном бакете пользователя и скачивает его сам. Если файл уже загружен
через существующий флоу хранилища, этот раздел можно пропустить, нужен только
его ключ.

### 1.1 Узнать бакет

```http
GET {resource_manager}/api/v1/resource/?resource_kind=Document
x-user-id: 1234
```

Ответ — массив ресурсов. Бакет — поле `id` того элемента, у которого
`resource_type == "Document"` и `resource_owner == "User"`. Такой элемент
ровно один. Если его нет, у пользователя нет хранилища, и задача завершится
ошибкой «Не найдено хранилище пользователя».

### 1.2 Загрузить файл

```http
PUT {watchtower}/api/v1/cloud/{bucket}/file/upload
Content-Type: multipart/form-data

prefix = documents            (необязательно, папка внутри бакета)
files  = <файл>               (имя файла берётся из filename части)
```

Успех — `200` или `201`. Object key файла получается склейкой:
`{prefix}/{filename}`, без префикса просто `{filename}`. Именно эту строку
дальше передают как `file_path`.

Важно про имя файла: парсер выбирается **по расширению**, поэтому расширение
обязано быть настоящим и из списка поддерживаемых (раздел 7). MIME-тип не
проверяется.

## 2. Поставить задачу

```http
POST http://dev.sova.local:10020/api/v1/broker/publish
Content-Type: application/json
x-user-id: 1234

{
  "task_type": "document-parser.translate",
  "payload": {
    "file_path": "documents/report.pdf",
    "source_language": "auto",
    "target_language": "ru",
    "output_prefix": null,
    "parse_images": false,
    "include_image_in_output": false,
    "full_vlm_pdf_parse": false
  }
}
```

Минимально достаточно одного `file_path`: у остальных полей есть значения по
умолчанию, показанные выше. Можно передавать полный объект всегда, это
безопасно.

Ответ `200`:

```json
{ "task_key": "1234:document-parser:5fb0b68c-2259-47d8-8e72-3dc517ac6d4d" }
```

Поля `payload`:

| Поле | Тип | По умолчанию | Описание |
| --- | --- | --- | --- |
| `file_path` | string | **обязательное** | Object key внутри бакета: без имени бакета, без ведущего слэша. Обратные слэши приводятся к прямым. |
| `source_language` | string | `"auto"` | Код языка оригинала по ISO 639 (`en`, `de`, `eng`) или `auto` для автоопределения по первым абзацам. |
| `target_language` | string | `"ru"` | Код целевого языка по ISO 639. |
| `output_prefix` | string \| null | `translated/{task_id}` | Папка в бакете для переведённого файла. Обычно не передают. |
| `parse_images` | boolean | `false` | Распознавать текст на встроенных картинках через VLM (OCR). Нужно для сканов и документов с картинками-текстом. Заметно дольше. |
| `include_image_in_output` | boolean | `false` | Сохранять картинки из оригинала в итоговом `.docx`. Без флага на их месте остаются заглушки. Файл получается заметно больше. |
| `full_vlm_pdf_parse` | boolean | `false` | Отдать PDF целиком в VLM вместо разбора через Docling. Для «сложных» PDF со сбитой вёрсткой или без текстового слоя. Только для `.pdf`, заметно дольше. |

Правила:

- `task_id` не придумывать: его генерирует гейтвей и возвращает третьим
  сегментом `task_key`.
- `user_id` в теле не нужен. Если заголовка `x-user-id` нет, гейтвей возьмёт
  `user_id` из тела, но при этом бакет всё равно ищется по этому значению,
  так что проще всегда слать заголовок.
- Пустая строка в языке равнозначна значению по умолчанию. Неизвестный код
  языка даёт `ERROR` без повторов.
- Неизвестные поля в `payload` игнорируются.
- `parse_images` и `full_vlm_pdf_parse` требуют доступной VLM и удлиняют
  задачу в разы. В интерфейсе их лучше держать выключенными по умолчанию и
  давать как явные опции («Распознать текст на картинках», «Сложный PDF»).
- **Успешный ответ означает только «задача принята»**, не «переведена». Всё
  дальше читается из webhook manager.
- **Не ретраить постановку.** Повторный `publish` создаёт вторую задачу с тем
  же файлом. Ошибки самой задачи сервис переигрывает сам (раздел 6).

Ошибки гейтвея (тело всегда `{"message": "..."}`):

| Код | Причина |
| --- | --- |
| 400 | Нет `x-user-id` и нет `user_id` в теле, либо битый JSON |
| 404 | Неизвестный `task_type` |
| 415 | Не `application/json` |
| 422 | JSON валиден, но данные невалидны |
| 503 | Брокер или webhook manager недоступны. Можно повторить позже, это единственный случай, когда повтор `publish` уместен |

## 3. Следить за выполнением

Фронтенд **опрашивает список всех задач пользователя** в webhook manager и
сам отбирает из него нужные. Один запрос раз в 1–2 секунды обслуживает все
активные задачи пользователя сразу, а заодно показывает задачи, поставленные
с другого устройства или до перезагрузки страницы. Чтение одной задачи по
ключу (3.3) нужно только как вспомогательное.

### 3.1 Список задач пользователя

```http
GET http://dev.sova.local:10001/api/v1/storage/tasks
X-User-ID: 1234
```

Можно и `?user_id=1234` в query; заголовок приоритетнее. Ответ `200` — массив:

```json
[
  {
    "task_id": "5fb0b68c-2259-47d8-8e72-3dc517ac6d4d",
    "user_id": "1234",
    "service": "document-parser",
    "progress": { "status": "PROCESSING", "progress": 42.5 },
    "created_at": "2026-09-07T10:00:00.000Z",
    "updated_at": "2026-09-07T10:03:12.000Z",
    "response_data": "{\"original_language\":\"en\",\"target_language\":\"ru\",\"original_file\":\"documents/report.pdf\",\"translated_file\":\"\",\"text_status\":\"Перевожу... 51/120 элементов\",\"error\":null}",
    "expire": 1712
  }
]
```

Коды: `404` — у пользователя нет ни одной задачи, `422` — `user_id` не
передан ни в query, ни в заголовке, `503` — Redis недоступен.

Правила работы со списком:

- **`404` означает «список пуст»**, а не ошибку. Так устроен сервис.
- В списке все задачи пользователя по всем сервисам. Отбирайте свои по
  `service == "document-parser"`.
- Задача из списка сопоставляется с `task_key`, полученным при постановке:
  ключ равен `${user_id}:${service}:${task_id}`. Проще всего хранить на
  фронте набор своих `task_key` и находить их в списке по этой склейке,
  либо просто показывать всё, что вернулось с `service == "document-parser"`.
- `expire` — секунд до удаления записи из Redis (TTL, см. ниже). Есть только
  в списке, у одиночной задачи его нет.
- Порядок элементов не гарантирован. Сортируйте по `created_at` сами.
- Опрашивайте раз в 1–2 секунды, пока у пользователя есть нетерминальные
  задачи, реже — когда все завершены. Чаще нет смысла: document-parser
  обновляет прогресс примерно на каждых 5% переведённых элементов.

### 3.2 Что лежит в задаче

**Статусы `progress.status`:** `PENDING`, `AWAITING`, `PROCESSING`, `READY`,
`ERROR`, `CANCELLED`. Терминальные: `READY`, `ERROR`, `CANCELLED`. Пока статус
не терминальный, работа идёт.

**Шкала `progress.progress`:** число от 0 до 100. Гейтвей создаёт задачу с
`PENDING` и `0`, дальше её двигает document-parser.

**`response_data` — это JSON в строке**, его нужно `JSON.parse`. Содержимое:

| Поле | Описание |
| --- | --- |
| `original_language` | Язык оригинала. При `auto` заменяется на определённый язык, как только он известен. |
| `target_language` | Целевой язык. |
| `original_file` | Object key оригинала в бакете. |
| `translated_file` | Object key переведённого `.docx`. Заполняется только при `READY`. |
| `text_status` | Человекочитаемый статус. Можно показывать пользователю как есть. |
| `error` | Техническая причина, если статус `ERROR`. |

Ловушка: **сразу после постановки** `response_data` содержит не эти поля, а
копию отправленного `payload` (так его записывает гейтвей). До первого
обновления от document-parser поля `text_status` там нет. Парсите
`response_data` терпимо к отсутствующим полям. Зато из этой копии можно
достать `file_path` и показать имя файла ещё до старта обработки.

Что увидит пользователь по ходу задачи:

| Статус | Прогресс | `text_status` |
| --- | --- | --- |
| `PENDING` | 0 | нет (в `response_data` ещё копия payload) |
| `PROCESSING` | 5 | Готовлю исходный файл... |
| `PROCESSING` | 10 | Оригинал готов. Парсинг документа... |
| `PROCESSING` | 15 | Начинаю перевод... |
| `PROCESSING` | 15 → 93 | Перевожу... 51/120 элементов |
| `PROCESSING` | 95 | Загружаю переведённый файл... |
| `READY` | 100 | Готово |
| `READY` | 100 | Готово. Не переведено элементов: N |
| `PROCESSING` | 0 | Временный сбой, повторная попытка 1/3 |
| `ERROR` | последний | текст причины, дублируется в `error` |
| `CANCELLED` | последний | Задача отменена |

`READY` с текстом «Не переведено элементов: N» — частичный успех: файл готов,
но часть фрагментов осталась на языке оригинала с пометкой
«(ошибка запроса, переведите вручную)». Показывайте это пользователю, файл при
этом полноценный.

Парсинг занимает от секунд до минут (лимит 15 минут), перевод большого
документа — десятки минут (лимит на всю задачу 50 минут). Между шагами
10 и 15 прогресс может стоять долго, это нормально.

**TTL записи.** Задача хранится в Redis ограниченное время (в dev-контуре 30
минут, по умолчанию у сервиса 60) с момента последнего обновления, остаток
виден в поле `expire`. После этого задача исчезает из списка. Поэтому
прочитайте `translated_file` сразу, как увидели `READY`, и сохраните на своей
стороне. Исчезновение активной задачи из списка практически невозможно:
каждое обновление прогресса продлевает TTL.

### 3.3 Одна задача по ключу

Вспомогательный запрос: проверить конкретную задачу сразу после постановки
или по сохранённому ключу после перезагрузки.

```http
GET http://dev.sova.local:10001/api/v1/storage/task?key=1234:document-parser:5fb0b68c-2259-47d8-8e72-3dc517ac6d4d
```

Ответ `200` — один объект задачи в том же формате, что элемент списка, но без
`expire`. Коды: `404` — задачи с таким ключом нет (не создана или истёк TTL),
`422` — ключ не в формате `a:b:c`, `503` — Redis недоступен.

### 3.4 WebSocket

```
ws://dev.sova.local:10001/api/v1/storage/ws
X-User-ID: 1234
```

Протокол: сервер сразу шлёт `{"status": "PING"}`; клиент отвечает любым
текстом (например, `PONG`); дальше сервер раз в секунду присылает тот же
массив задач, что в 3.1.

**Из браузера этот канал бесполезен:** браузерный `WebSocket` не умеет
выставлять произвольные заголовки, а без `X-User-ID` сервер подписывает на
пользователя `guest`. Используйте опрос списка из 3.1. WebSocket пригоден
только через прокси, который подставит заголовок.

## 4. Забрать результат

При `READY` в `response_data.translated_file` лежит object key переведённого
файла, например `translated/5fb0b68c-.../report_(переведённый).docx`. Имя
файла: `<имя оригинала без расширения>_(переведённый).docx`. Это всегда
`.docx`, независимо от формата исходника.

Это ключ, а не ссылка, сознательно: ссылка протухает, ключ живёт столько же,
сколько файл. Ссылку или байты получают у watchtower в момент, когда они нужны:

```http
POST {watchtower}/api/v1/cloud/{bucket}/file/download
Content-Type: application/json

{ "file_name": "translated/5fb0b68c-.../report_(переведённый).docx" }
```

Ответ `200` — байты файла. Либо временная ссылка для кнопки «Скачать»:

```http
POST {watchtower}/api/v1/cloud/{bucket}/file/share
Content-Type: application/json

{ "file_path": "translated/5fb0b68c-.../report_(переведённый).docx", "expired_secs": 3600 }
```

Ответ `{"message": "<url>"}`. Обратите внимание на разные имена поля:
`file_name` при скачивании и `file_path` при share, так в API watchtower.

## 5. Отменить задачу

```http
POST http://dev.sova.local:10020/api/v1/tasks/cancel?task_id=1234%3Adocument-parser%3A5fb0b68c-2259-47d8-8e72-3dc517ac6d4d
```

В `task_id` передаётся **весь `task_key`**, а не только UUID. Ответ `200`
`{"code": 200, "message": "ok"}`; `404` — задачи нет; `503` — webhook manager
недоступен.

Что происходит: гейтвей сразу ставит задаче `CANCELLED` с прогрессом 0. Для
интерфейса это уже терминальный статус, можно считать задачу отменённой.
document-parser проверяет статус на контрольных точках между этапами, выходит
из конвейера и ещё раз публикует `CANCELLED`, уже с последним прогрессом.
Внутри длинного этапа (парсинг, скачивание большого файла) выход может занять
минуты, это не ошибка. Оригинал и уже загруженные файлы из бакета не удаляются.

Отмена задачи в `READY`, `ERROR` или уже `CANCELLED` ничего не ломает, но
перепишет статус на `CANCELLED`, поэтому не показывайте кнопку отмены для
терминальных задач.

## 6. Ошибки и повторы: что делает сервис сам

- **Временные сбои** (недоступно хранилище, переводчик, сеть) сервис
  переигрывает сам: до 3 повторов с паузой 30 секунд. В это время задача
  вернётся в `PROCESSING` с прогрессом 0 и текстом «Временный сбой, повторная
  попытка k/3». Фронтенду ничего делать не нужно.
- **Постоянные ошибки** дают `ERROR` сразу, без повторов. Их чинят на стороне
  клиента:

| `text_status` | Что проверить |
| --- | --- |
| Формат файла не поддерживается | расширение файла, раздел 7 |
| Файл не найден в хранилище | `file_path` и бакет того ли пользователя |
| Не найдено хранилище пользователя | у пользователя нет персонального Document-ресурса |
| Файл слишком большой | лимит 200 МБ |
| Не удалось определить язык документа | передать `source_language` явно |
| Превышено время обработки | документ слишком большой или сложный; попробовать без `parse_images` |
| Не удалось обработать документ | файл битый или не соответствует расширению |
| Неверный формат данных задачи | `payload` не прошёл валидацию, смотреть `error` |

- После `ERROR` повторная постановка — это новая задача с новым `task_key`.
- Перезапуск сервиса задачу не теряет: незавершённое сообщение возвращается в
  очередь и доигрывается. Если задача долго висит в `PENDING` с прогрессом 0
  и без `text_status`, воркер не разбирает очередь; это вопрос к эксплуатации,
  а не повод ставить задачу заново.

## 7. Ограничения

| Параметр | Значение |
| --- | --- |
| Максимальный размер файла | 200 МБ |
| Лимит времени на задачу | 3000 с (50 минут) |
| Лимит времени на парсинг | 900 с (15 минут) |
| Параллельных задач на воркер | 3, остальные ждут в очереди в `PENDING` |

Поддерживаемые расширения: `.jpg` `.jpeg` `.png` `.tiff` `.bmp` `.webp`
`.docx` `.doc` `.rtf` `.odt` `.ott` `.ods` `.ots` `.odp` `.otp` `.pptx`
`.xlsx` `.pdf` `.txt` `.text` `.md` `.qmd` `.Rmd` `.rmd` `.html` `.epub`
`.eml` `.xbrl` `.xml`.

Значения взяты из dev-окружения; для другого контура перечитайте
`GET /api/v1/contract` у document-parser, там актуальные лимиты.

## 8. Пример кода (TypeScript)

```ts
const GATEWAY = "http://dev.sova.local:10020";
const WEBHOOK = "http://dev.sova.local:10001";
const SERVICE = "document-parser";

type TaskStatus = "PENDING" | "AWAITING" | "PROCESSING" | "READY" | "ERROR" | "CANCELLED";
const TERMINAL: TaskStatus[] = ["READY", "ERROR", "CANCELLED"];

interface Task {
  task_id: string;
  user_id: string;
  service: string;
  progress: { status: TaskStatus; progress: number };
  created_at: string;
  updated_at: string;
  response_data: string; // JSON в строке
  expire?: number;       // только в списке: секунд до удаления записи
}

interface TranslateResult {
  original_language?: string;
  target_language?: string;
  original_file?: string;
  translated_file?: string;
  text_status?: string;
  error?: string | null;
  file_path?: string; // есть только пока в response_data лежит копия payload
}

export const taskKeyOf = (t: Task) => `${t.user_id}:${t.service}:${t.task_id}`;

// Payload задачи document-parser.translate. Обязателен только file_path,
// остальное — значения по умолчанию сервиса.
interface TranslatePayload {
  file_path: string;
  source_language?: string;        // "auto" | код ISO 639
  target_language?: string;        // код ISO 639
  output_prefix?: string | null;   // папка результата, по умолчанию translated/{task_id}
  parse_images?: boolean;          // OCR картинок через VLM
  include_image_in_output?: boolean; // картинки оригинала в итоговом .docx
  full_vlm_pdf_parse?: boolean;    // PDF целиком в VLM
}

const DEFAULT_PAYLOAD: Required<Omit<TranslatePayload, "file_path">> = {
  source_language: "auto",
  target_language: "ru",
  output_prefix: null,
  parse_images: false,
  include_image_in_output: false,
  full_vlm_pdf_parse: false,
};

export async function submitTranslation(
  userId: string,
  payload: TranslatePayload,
): Promise<string> {
  const res = await fetch(`${GATEWAY}/api/v1/broker/publish`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-user-id": userId },
    body: JSON.stringify({
      task_type: "document-parser.translate",
      payload: { ...DEFAULT_PAYLOAD, ...payload },
    }),
  });
  if (!res.ok) throw new Error((await res.json()).message);
  return (await res.json()).task_key as string;
}

// Все задачи пользователя по document-parser. 404 = задач нет.
export async function listTasks(userId: string): Promise<Task[]> {
  const res = await fetch(`${WEBHOOK}/api/v1/storage/tasks`, {
    headers: { "X-User-ID": userId },
  });
  if (res.status === 404) return [];
  if (!res.ok) throw new Error(`webhook manager ${res.status}`);
  const tasks: Task[] = await res.json();
  return tasks
    .filter((t) => t.service === SERVICE)
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
}

// До первого обновления от document-parser здесь лежит копия payload,
// поэтому поля необязательные.
export function parseResult(task: Task): TranslateResult {
  try {
    const parsed = JSON.parse(task.response_data);
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
}

export const isTerminal = (t: Task) => TERMINAL.includes(t.progress.status);

// Опрос списка: раз в 1.5 с, пока есть активные задачи, иначе раз в 10 с.
export function pollTasks(
  userId: string,
  onUpdate: (tasks: Task[]) => void,
  signal: AbortSignal,
): void {
  const tick = async () => {
    if (signal.aborted) return;
    let delay = 10_000;
    try {
      const tasks = await listTasks(userId);
      onUpdate(tasks);
      if (tasks.some((t) => !isTerminal(t))) delay = 1_500;
    } catch (e) {
      console.warn("poll failed", e);
    }
    setTimeout(tick, delay);
  };
  void tick();
}

export async function cancelTask(taskKey: string): Promise<void> {
  const res = await fetch(`${GATEWAY}/api/v1/tasks/cancel?task_id=${encodeURIComponent(taskKey)}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error((await res.json()).message);
}

// Использование
// const key = await submitTranslation("1234", {
//   file_path: "documents/report.pdf",
//   source_language: "auto",
//   target_language: "ru",
//   parse_images: true,          // документ со сканами
//   full_vlm_pdf_parse: false,
// });
// pollTasks("1234", (tasks) => {
//   for (const t of tasks) {
//     const data = parseResult(t);
//     render(taskKeyOf(t), t.progress.status, t.progress.progress, data.text_status ?? "Задача принята");
//     if (t.progress.status === "READY" && data.translated_file) offerDownload(data.translated_file);
//   }
// }, controller.signal);
```

## 9. Чек-лист перед релизом

- [ ] Файл загружен в бакет пользователя, `file_path` — object key без имени бакета и без ведущего слэша.
- [ ] Расширение файла из списка раздела 7.
- [ ] `task_type` ровно `document-parser.translate`.
- [ ] Во все запросы уходит один и тот же `x-user-id`, и это владелец бакета.
- [ ] `task_key` из ответа гейтвея сохранён, задача из списка сопоставляется по склейке `user_id:service:task_id`.
- [ ] `response_data` парсится через `JSON.parse` и терпит отсутствие полей.
- [ ] Прогресс читается опросом списка задач пользователя, свои задачи отбираются по `service == "document-parser"`.
- [ ] `404` от списка задач трактуется как пустой список.
- [ ] Частота опроса снижается, когда у пользователя нет активных задач.
- [ ] `translated_file` читается сразу после `READY` и сохраняется у себя.
- [ ] Постановка не ретраится, кроме `503` от гейтвея.
- [ ] Кнопка отмены скрыта для терминальных задач и передаёт весь `task_key`.
