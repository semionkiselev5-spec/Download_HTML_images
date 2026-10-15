# Download_HTML_images

Выгрузка выдачи Яндекс Картинок по запросам из JSON в кликабельные HTML. Страницы можно сохранить на ноутбуке или опубликовать в S3 и получить ссылки. В выгрузках отключается программный блюр; исходные картинки и просмотрщик загружаются браузером при открытии HTML.

## Установка

Нужен Python 3.10 или новее. Скачайте проект через **Code → Download ZIP** и распакуйте архив либо используйте Git:

```bash
git clone https://github.com/semionkiselev5-spec/Download_HTML_images.git
cd Download_HTML_images
```

Создайте и активируйте виртуальное окружение по [короткой инструкции](RUN_RU.md), затем установите зависимости:

```bash
python -m pip install -r requirements.txt
```

## Локальная проверка без S3

```bash
python sbs_capture_single.py --input input.json --config config.images.standard.json --output runs-local --local-only
```

Результаты появятся в `runs-local/<идентификатор запуска>/`. Откройте сохранённый HTML в браузере с доступом к интернету.

## Запуск со своим JSON и публикацией в S3

Сохраните свой список запросов в JSON и задайте настройки хранилища в `config.images.standard.json`. Передайте `AWS_ACCESS_KEY_ID` и `AWS_SECRET_ACCESS_KEY` через окружение или используйте настроенный AWS-профиль. Способы ввода ключей описаны в [инструкции запуска](RUN_RU.md#публикация-в-s3).

```bash
python sbs_capture_single.py --input "my_queries.json" --config config.images.standard.json --output runs-s3
```

Замените путь после `--input` на свой. Без `--local-only` программа загружает HTML в S3. После успешной публикации ссылка появится в строке `HTML:`, поле `s3_url` и файле `urls.txt` внутри папки запуска.

## Файлы проекта

| Файл | Назначение |
| --- | --- |
| [sbs_capture_single.py](sbs_capture_single.py) | Исходный скрипт выгрузки |
| [requirements.txt](requirements.txt) | Зависимости Python |
| [input.json](input.json) | Пример списка запросов; замените его содержимое на своё |
| [config.images.standard.json](config.images.standard.json) | Настройки выдачи и S3 |
| [.env.example](.env.example) | Пустой шаблон ключей S3; подключается вручную |
| [RUN_RU.md](RUN_RU.md) | Короткая инструкция для macOS, Linux и Windows |

Скрипт принимает объект, массив объектов или JSONL с полями `query`, `region_id`, `device`. Поддерживаемые устройства: `DESKTOP`, `ANDROID`, `IPHONE`. Запуск со своим JSON описан в [RUN_RU.md](RUN_RU.md).
