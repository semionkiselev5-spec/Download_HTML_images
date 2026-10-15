# Как запустить Download_HTML_images

## Скачать проект

Установите [Python 3.10 или новее](https://www.python.org/downloads/). В Windows включите **Add Python to PATH**, если установщик предлагает этот пункт.

На [GitHub](https://github.com/semionkiselev5-spec/Download_HTML_images) нажмите **Code → Download ZIP** и распакуйте архив. Откройте терминал и перейдите в распакованную папку:

```text
cd "путь к папке Download_HTML_images-main"
```

Все команды ниже выполняются в этой папке.

## Установить зависимости

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

**Windows — командная строка cmd:**

```bat
py -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
```

## Подготовить свой JSON

Создайте в папке проекта файл `my_queries.json` в кодировке UTF-8:

```json
[
  {"query": "горный пейзаж", "region_id": 213, "device": "ANDROID"},
  {"query": "современная архитектура", "region_id": 213, "device": "DESKTOP"}
]
```

Замените запросы и регионы на свои. `region_id` — числовой ID региона поиска; `device` — `DESKTOP`, `ANDROID` или `IPHONE`.

## Проверить без S3

```bash
python sbs_capture_single.py --input "my_queries.json" --config config.images.standard.json --output runs-local --local-only
```

Откройте HTML из `runs-local/<папка запуска>/` в браузере с интернетом. Этот режим не создаёт публичные ссылки. Программный блюр в выгрузке отключается автоматически.

## Публикация в S3

В `config.images.standard.json` замените настройки `s3.endpoint`, `s3.region`, `s3.bucket` и `s3.prefix` на свои. Нужны ключи с правом записи и публичное чтение HTML; значение `s3.acl` должно поддерживаться вашим хранилищем.

**macOS / Linux:** скопируйте `.env.example` в `.env`, впишите свои `AWS_ACCESS_KEY_ID` и `AWS_SECRET_ACCESS_KEY`, затем подключите файл:

```bash
set -a
source ./.env
set +a
```

**Windows, cmd:** задайте ключи в текущем окне, заменив заглушки своими значениями:

```bat
set "AWS_ACCESS_KEY_ID=ВАШ_ACCESS_KEY"
set "AWS_SECRET_ACCESS_KEY=ВАШ_SECRET_KEY"
```

Запустите выгрузку:

```bash
python sbs_capture_single.py --input "my_queries.json" --config config.images.standard.json --output runs-s3
```

Ссылки появятся в терминале после `HTML:` и в `runs-s3/<папка запуска>/urls.txt`. В `results.json` каждому обработанному запросу соответствует запись с `s3_url` либо причиной ошибки. Ключи и файл `.env` не загружайте на GitHub.

Для другого JSON замените путь после `--input`; пути с пробелами оставляйте в кавычках. При новом открытии терминала снова активируйте `.venv` и, для S3, задайте ключи.
