#!/usr/bin/env python3
"""Выгрузка поисковой выдачи картинок и видео в HTML и S3, в одном файле.

Установка зависимостей (Python 3.10+):
    python -m pip install beautifulsoup4==4.14.3 boto3==1.42.14 playwright==1.57.0 requests==2.32.5
Для видео также установите браузер:
    python -m playwright install chromium

Входной JSON — объект, массив объектов или JSONL:
    [{"query": "горный пейзаж", "region_id": 213, "device": "DESKTOP"}]
Устройства: DESKTOP, ANDROID, IPHONE.

Запуск с настройками картинок, встроенными ниже в DEFAULT_CONFIG:
    python sbs_capture_single.py --input queries.json --output runs-manual
С отдельным конфигом:
    python sbs_capture_single.py --input queries.json --config config.images.standard.json --output runs-manual
Видео:
    python sbs_capture_single.py --input queries.json --service video --output runs-manual
Локальная проверка без S3:
    python sbs_capture_single.py --input queries.json --local-only --output runs-manual

Доступ к S3: AWS_ACCESS_KEY_ID и AWS_SECRET_ACCESS_KEY в окружении
или стандартный AWS_PROFILE. Ключи в этот файл не записываются.
В конфиге можно менять host, test_ids, cgi, desktop_min_items,
desktop_max_pages и настройки s3. Входной JSON задаёт запрос и регион.

Результат: runs-manual/<идентификатор запуска>/results.json и JSONL в stdout.
Для успешных загрузок поле s3_url содержит проверенную публичную ссылку.
Также ссылка печатается отдельной строкой HTML: в терминале (stderr),
а все публичные ссылки сохраняются в urls.txt рядом с results.json.
Также сохраняются HTML и метаданные каждого запроса. Ошибка одного запроса
не прерывает остальные. Код выхода: 0 — успех, 1 — ошибки отдельных
запросов, 2 — ошибка настройки или запуска.

Desktop: цель 90 картинок, максимум 5 страниц; последняя страница целиком.
Если выдача закончилась раньше, фактическое число отражается в метаданных.
Мобильная выдача и видео сохраняются с одной страницы.
Просмотрщик картинок сохраняет исходную механику и ссылки на сайт-источник.
Обязательное правило каждой выгрузки: блюр отключён у всех изображений —
в выдаче, миниатюрах видео и просмотрщике, на DESKTOP, ANDROID и IPHONE.
Это правило действует также для --html; включить блюр через конфиг нельзя.
Параметр remove_blur принудительно равен true, даже если внешний конфиг
содержит remove_blur: false.
Картинки сохраняются как в yandex old: исходные URL миниатюр, оригиналов,
стилей и скриптов остаются в HTML, а ресурсы загружает браузер при просмотре.
При выгрузке картинок скрипт не скачивает изображения или таблицы стилей
и не встраивает их в base64. Недоступность отдельного оригинала не мешает
сохранить HTML. Блюр отключается в состоянии приложения, CSS и JavaScript.
Полноразмерные изображения, шрифты и
оригинальные скрипты просмотрщика могут требовать подключения к интернету.
Собственные CSS и JavaScript встроены ниже; соседние файлы проекта не нужны.
"""

from dataclasses import dataclass, asdict
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import json
from bs4 import BeautifulSoup
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
import time
import requests
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import TimeoutError as BrowserTimeout
import base64
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin
import hashlib
from urllib.parse import quote
import boto3
from botocore.config import Config
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import os
import sys
import uuid

DEFAULT_CONFIG = json.loads(r'''{
  "service": "images",
  "host": "https://yandex.ru",
  "test_ids": [],
  "cgi": {},
  "remove_blur": true,
  "desktop_min_items": 90,
  "desktop_max_pages": 5,
  "timeout_seconds": 60,
  "settle_ms": 800,
  "s3": {
    "endpoint": "https://s3.twcstorage.ru",
    "region": "ru-1",
    "bucket": "bac33a93-fc8a-445a-b967-41d71601c7a5",
    "prefix": "sbs-capture",
    "acl": "public-read"
  }
}''')

NO_BLUR_CSS = r'''
/* Mandatory for every export, including dynamically opened viewers.
   Clear filters on ancestors too: a sharp img can inherit a blurred wrapper. */
html[data-sbs-unblur="true"],html[data-sbs-unblur="true"] *,
html[data-sbs-unblur="true"] *::before,html[data-sbs-unblur="true"] *::after{
  -webkit-filter:none!important;filter:none!important;
  -webkit-backdrop-filter:none!important;backdrop-filter:none!important;
}
'''

NATIVE_CSS = NO_BLUR_CSS + r'''
/* Mobile frame and viewer sizing from the supplied Yandex Old reference. */
html[data-sbs-device="ANDROID"],html[data-sbs-device="IPHONE"]{background:#d0d0d0!important}
html:not([data-sbs-device="DESKTOP"]) body{max-width:390px!important;min-height:844px!important;margin:0 auto!important;overflow-x:hidden!important;box-shadow:0 0 40px rgba(0,0,0,.5);position:relative}
html:not([data-sbs-device="DESKTOP"]) .ViewerScene{width:min(390px,100vw)!important;left:max(0px,calc((100vw - 390px)/2))!important;right:auto!important}
.ImagesTouchViewer-ImageContainer .ImagesContentImage,.ImagesTouchViewer-ImageContainer .ImagesContentImage-Cover{max-width:100%!important}
.ImagesTouchViewer-ImageContainer .ImagesViewerImage-ImageElement{min-width:0!important;min-height:0!important;max-width:100%!important;height:auto!important}
[class*="Distribution"],.SerpPage>:not(.SerpList):not(.ViewerScene):not(.ImagesViewer){display:none!important}
.YndxBug,#yndxbug,.TechTool,.DevToolsPanel,.MessageBox_layout_tooltip,[role="tooltip"],.ulitochka{display:none!important}
'''

NATIVE_JS = r'''
/* The native app owns opening, closing, swiping, title, domain and source URL.
   This adapter freezes capture controls, enforces no blur and forwards arrows. */
(() => {
  'use strict';
  const snapshotPath = location.pathname;
  for (const method of ['pushState', 'replaceState']) {
    const original = history[method];
    history[method] = function(state, title, value) {
      if (value != null) {
        const url = new URL(value, location.href);
        value = snapshotPath + url.search + url.hash;
      }
      return original.call(this, state, title, value);
    };
  }
  function sourceURL(value) {
    const url = new URL(value, location.href);
    if (url.origin === location.origin && url.pathname.startsWith('/images/')) {
      const source = new URL(document.documentElement.dataset.sbsSource);
      url.protocol = source.protocol;
      url.host = source.host;
    }
    return url.href;
  }
  // A saved S3 path is not an image-search API. Keep native resource requests
  // and modifier-click destinations on the configured source host.
  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url, ...args) {
    return open.call(this, method, sourceURL(url), ...args);
  };
  function prepare() {
    const filters = ['filter', '-webkit-filter', 'backdrop-filter', '-webkit-backdrop-filter'];
    // Inline overrides also beat highly specific native !important selectors.
    // Reset every ancestor: parent filters blur all of their children.
    const imageLayers = new Set();
    document.querySelectorAll('img,picture,video,canvas,[class*="Image"],[class*="Thumb"]').forEach(image => {
      for (let el = image; el; el = el.parentElement) imageLayers.add(el);
    });
    imageLayers.forEach(el => {
      for (const property of filters) {
        if (el.style.getPropertyValue(property) !== 'none' || el.style.getPropertyPriority(property) !== 'important') {
          el.style.setProperty(property, 'none', 'important');
        }
      }
    });
    // Native scripts can restore an inline filter after hydration or swiping.
    document.querySelectorAll('[style]').forEach(el => {
      for (const property of filters) {
        if (/blur\s*\(/i.test(el.style.getPropertyValue(property))) {
          el.style.setProperty(property, 'none', 'important');
        }
      }
    });
    document.querySelectorAll('.SerpItem a, .ViewerScene a, .ImagesViewer a').forEach(a => {
      if (a.hasAttribute('href')) a.href = sourceURL(a.href);
      if (a.target !== '_blank') a.target = '_blank';
      if (a.rel !== 'noopener noreferrer') a.rel = 'noopener noreferrer';
    });
    document.querySelectorAll('.HeaderPhone input, .HeaderPhone textarea, .HeaderPhone button, .FetchListButton-Button').forEach(el => {
      if (!el.disabled) el.disabled = true;
    });
  }
  document.addEventListener('DOMContentLoaded', () => {
    prepare();
    new MutationObserver(prepare).observe(document.documentElement, {
      childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class']
    });
  });
  window.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey ||
        !['ArrowLeft', 'ArrowRight'].includes(event.key) || event.target.closest?.('input,textarea,select,[contenteditable="true"]')) return;
    const selector = event.key === 'ArrowLeft'
      ? '.ImagesViewer-ButtonPrev, .ImagesTouchViewer-ClickableSide_left'
      : '.ImagesViewer-ButtonNext, .ImagesTouchViewer-ClickableSide_right';
    const visible = el => el.getClientRects().length && getComputedStyle(el).visibility === 'visible';
    const viewer = Array.from(document.querySelectorAll('.ViewerScene,.ImagesViewer')).find(visible);
    if (!viewer) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const button = Array.from(viewer.querySelectorAll(selector)).find(el => visible(el) && !el.disabled &&
      el.getAttribute('aria-disabled') !== 'true' && !el.classList.contains('CircleButton_disabled'));
    if (button) button.click();
  }, true);
})();
'''

VIDEO_CSS = NO_BLUR_CSS + r'''
#sbs-banner{position:relative;z-index:10;padding:9px 16px;background:#fff8d8;color:#433c26;font:13px/1.4 system-ui;border-bottom:1px solid #e7dbad;overflow-wrap:anywhere}
'''


# ========================================================================
# models
# ========================================================================

@dataclass(frozen=True)
class Query:
    query: str
    region_id: int
    device: str

    @classmethod
    def load(cls, obj):
        if not isinstance(obj, dict):
            raise ValueError("Each input must be an object")
        text = obj.get("query")
        region = obj.get("region_id")
        device = str(obj.get("device", "")).upper()
        device = {"PC": "DESKTOP", "MOBILE": "ANDROID", "IOS": "IPHONE"}.get(device, device)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("query must be a nonempty string")
        if isinstance(region, bool) or not str(region).isdigit():
            raise ValueError("region_id must be a nonnegative integer")
        if device not in {"DESKTOP", "ANDROID", "IPHONE"}:
            raise ValueError("device must be DESKTOP, ANDROID or IPHONE")
        return cls(text, int(region), device)

    def as_dict(self):
        return asdict(self)


@dataclass
class Item:
    id: str
    position: int
    title: str
    thumbnail_url: str
    image_url: str
    page_url: str
    censored: bool = False


def http_url(value, base="https://yandex.ru"):
    from urllib.parse import urljoin
    if not isinstance(value, str) or not value.strip():
        return ""
    value = urljoin(base, value)
    p = urlsplit(value)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        return ""
    return value


def search_url(task, config):
    service = config.get("service", "images")
    if service not in {"images", "video"}:
        raise ValueError("service must be images or video")
    host = config.get("host", "https://yandex.ru")
    p = urlsplit(host)
    if not http_url(host) or p.path not in {"", "/"} or p.query or p.fragment:
        raise ValueError("host must be an HTTP(S) origin, without path or parameters")
    mobile = task.device != "DESKTOP"
    path = config.get("mobile_path" if mobile else "desktop_path")
    path = path or f"/{service}/{'touch/' if mobile else ''}search"
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("Search path must be relative to host")
    q = dict(config.get("cgi", {}))
    if {"text", "lr"} & q.keys():
        raise ValueError("cgi cannot override query/region_id: use the input JSON")
    q.update(text=task.query, lr=str(task.region_id))
    ids = config.get("test_ids", [])
    if ids:
        q[config.get("test_id_param", "test-id")] = ",".join(map(str, ids))
    return urlunsplit((p.scheme, p.netloc, path, urlencode(q, doseq=True), ""))


# ========================================================================
# parser
# ========================================================================

class ParseError(ValueError):
    pass


def states(soup):
    for node in soup.select("[data-state], [data-bem]"):
        for attr in ("data-state", "data-bem"):
            try:
                yield json.loads(node.get(attr, ""))
            except (ValueError, TypeError):
                pass


def image_item(raw, position, base):
    viewer = raw.get("viewerData", {})
    snippet = raw.get("snippet") or viewer.get("snippet") or {}
    previews = viewer.get("preview") or []
    page = snippet.get("url") or raw.get("page_url")
    if not page:
        page = next((p.get("siteHref") for p in previews if p.get("siteHref")), "")
    original = raw.get("origUrl") or raw.get("image_url")
    thumb = raw.get("image") or raw.get("thumb", {}).get("url") or viewer.get("thumb", {}).get("url")
    original = original or next((p.get("url") for p in previews if p.get("url")), thumb)
    return Item(str(raw.get("id", position)), position,
                str(snippet.get("title") or raw.get("alt") or ""),
                http_url(thumb, base), http_url(original, base), http_url(page, base),
                bool(raw.get("censored") or raw.get("censoredShock") or
                     viewer.get("censored") or viewer.get("censoredShock")))


def parse_images(soup, base):
    for state in states(soup):
        items = state.get("initialState", {}).get("serpList", {}).get("items", {})
        entities = items.get("entities", {})
        keys = items.get("keys", list(entities))
        if entities:
            return [image_item(entities[k], i, base) for i, k in enumerate(keys) if k in entities]
    # Legacy BEM layout. Keep DOM order, including repeated images.
    found = []
    for node in soup.select("[data-bem]"):
        try:
            raw = json.loads(node["data-bem"]).get("serp-item")
        except ValueError:
            continue
        if raw:
            found.append(image_item(raw, len(found), base))
    return found


def parse(html, service, base):
    soup = BeautifulSoup(html, "html.parser")
    if service == "images":
        items = parse_images(soup, base)
    else:
        items = parse_videos(soup, base)
    if not items:
        raise ParseError("No result records found. Check for CAPTCHA, empty results or changed markup; no S3 page published.")
    missing = [x.position for x in items if not x.page_url or not x.thumbnail_url]
    if missing:
        raise ParseError(f"Missing source URL or thumbnail at positions {missing[:10]}; cannot publish a misleading snapshot")
    return soup, items


def parse_videos(soup, base):
    # DOM is authoritative after hydration. The initial data-state can be empty.
    runtime_node = soup.select_one("#UniAppVideo-PreloadedState")
    try:
        runtime_state = json.loads(runtime_node.get_text()) if runtime_node else {}
    except ValueError:
        runtime_state = {}
    clips = runtime_state.get("clips", {}).get("items", {})
    dups = runtime_state.get("clips", {}).get("dups", {})
    dom_items = []
    for card in soup.select(".VideoSnippet"):
        title = card.select_one(".VideoSnippet-Title")
        host = card.select_one(".VideoHostExtended-Host[href]")
        poster = card.select_one("video[poster], img[src]")
        if not title or not poster:
            raise ParseError("Video card lacks title or poster")
        import re
        match = re.search(r"/preview/([^?/#]+)", title.get("href", ""))
        raw = (dups.get(match.group(1)) or clips.get(match.group(1), {})) if match else {}
        parent = clips.get(raw.get("parentClipId"), raw)
        page_url = host.get("href") if host else raw.get("host", {}).get("href") or raw.get("url")
        if not page_url:
            raise ParseError("Video card lacks a source link in DOM and structured data")
        dom_items.append(Item(match.group(1) if match else str(len(dom_items)), len(dom_items),
                              title.get_text(" ", strip=True),
                              http_url(poster.get("poster") or poster.get("src"), base),
                              "", http_url(page_url, base), bool(parent.get("censored") or parent.get("censoredShock"))))
    if dom_items:
        return dom_items
    result = []
    candidates = [x.get("preloadedState", {}) for x in states(soup)]
    runtime = soup.select_one("#UniAppVideo-PreloadedState")
    if runtime:
        try:
            candidates.insert(0, json.loads(runtime.get_text()))
        except ValueError:
            pass
    for state in candidates:
        clips = state.get("clips", {}).get("items", {})
        for entry in state.get("pages", {}).get("search", {}).get("serpItems", []):
            if entry.get("type") == "videoSnippet":
                raw = clips.get(entry.get("props", {}).get("videoId"))
                if raw:
                    result.append(raw)
        if result:
            break
    return normalize_videos(result, base)


def normalize_videos(records, base):
    items = []
    for i, raw in enumerate(records):
        video = raw.get("video", raw)
        url = video.get("host", {}).get("href") or video.get("url") or video.get("page_url")
        thumb = video.get("thumb") or video.get("thumbnail") or video.get("preview")
        if isinstance(thumb, dict):
            thumb = thumb.get("url") or thumb.get("posterSrc")
        items.append(Item(str(video.get("videoId") or video.get("id", i)), i, str(video.get("cleanTitle") or video.get("title", "")),
                          http_url(thumb, base), "", http_url(url, base)))
    return items


# ========================================================================
# pagination
# ========================================================================

def native_state(soup):
    root = soup.select_one('[id^="ImagesApp"][data-state]')
    if root is None:
        raise ParseError('Native image state is missing')
    state = json.loads(root['data-state'])
    if not isinstance(state.get('initialState', {}).get('serpList', {}).get('items'), dict):
        raise ParseError('Native image list is missing')
    return root, state


def next_page_url(link, current_url, initial_url):
    candidate = urlsplit(urljoin(current_url, link))
    initial = urlsplit(initial_url)
    if (candidate.scheme, candidate.netloc, candidate.path) != (initial.scheme, initial.netloc, initial.path):
        raise ParseError('Pagination changed the search host or path')
    source = parse_qs(initial.query, keep_blank_values=True)
    next_params = parse_qs(candidate.query, keep_blank_values=True)
    for key, values in source.items():
        if key != 'p' and key in next_params and next_params[key] != values:
            raise ParseError(f'Pagination changed requested parameter: {key}')
    page = next_params.get('p', [''])
    previous = parse_qs(urlsplit(current_url).query).get('p', ['0'])
    if len(page) != 1 or not page[0].isdigit() or int(page[0]) <= int(previous[0]):
        raise ParseError('Pagination did not advance to the next page')
    # Next links sometimes omit experimental parameters; retain the full input.
    pairs = [(k, v) for k, v in parse_qsl(candidate.query, keep_blank_values=True)
             if k not in source or k == 'p']
    pairs.extend((k, v) for k, v in parse_qsl(initial.query, keep_blank_values=True) if k != 'p')
    return urlunsplit((candidate.scheme, candidate.netloc, candidate.path, urlencode(pairs), ''))


def collect_desktop(soup, items, initial_url, fetch_page, minimum, max_pages):
    root, state = native_state(soup)
    combined = state['initialState']['serpList']
    records = combined['items']['entities']
    keys = list(combined['items'].get('keys', records))
    seen_sources = {(item.image_url, item.page_url) for item in items}
    initial_count = len(items)
    source_pages = [initial_url]
    latest, current_url = combined, initial_url
    stop_reason = 'target_reached'

    while len(keys) < minimum:
        link = latest.get('navi', {}).get('next')
        if not link:
            stop_reason = 'end_of_results'
            break
        if len(source_pages) >= max_pages:
            raise ParseError(f'Collected {len(keys)} images; target {minimum} not reached within {max_pages} pages')
        url = next_page_url(link, current_url, initial_url)
        next_soup, next_items, current_url = fetch_page(url)
        _, next_state = native_state(next_soup)
        latest = next_state['initialState']['serpList']
        next_records = latest['items']['entities']
        added = 0
        for item in next_items:
            if item.id in records or (item.image_url, item.page_url) in seen_sources:
                continue
            records[item.id] = next_records[item.id]
            keys.append(item.id)
            seen_sources.add((item.image_url, item.page_url))
            added += 1
        source_pages.append(current_url)
        if not added:
            stop_reason = 'no_new_items'
            break

    combined['items']['keys'] = keys
    # Native desktop HTML starts with a skeleton; React lays out these records
    # at the actual display width and keeps its own original viewer handlers.
    combined['lastPage'] = latest.get('lastPage', combined.get('lastPage', 0))
    combined['loadNextState'] = 'finished'
    combined['prefetchNext'] = False
    combined.setdefault('autoload', {})['enabled'] = False
    combined.setdefault('navi', {})['next'] = ''
    root['data-state'] = json.dumps(state, ensure_ascii=True, separators=(',', ':'))
    merged_items = parse_images(soup, initial_url)
    if len(merged_items) != len(keys):
        raise ParseError('Merged native state lost result cards')
    return soup, merged_items, {
        'initial_items': initial_count, 'items_collected': len(merged_items),
        'pages_collected': len(source_pages), 'source_pages': source_pages,
        'minimum_items': minimum, 'target_reached': len(merged_items) >= minimum,
        'stop_reason': stop_reason,
    }


# ========================================================================
# capture
# ========================================================================

class Capture:
    def __init__(self, playwright, config):
        self.p = playwright
        self.config = config
        self.browser = (playwright.chromium.launch(headless=True)
                        if config.get("service", "images") == "video" else None)

    def close(self):
        if self.browser:
            self.browser.close()

    def run(self, task):
        service = self.config.get("service", "images")
        url = search_url(task, self.config)
        profiles = {"ANDROID": "Pixel 7", "IPHONE": "iPhone 13"}
        desktop_viewport = ({"width": 1920, "height": 1080} if service == 'images'
                            else {"width": 1365, "height": 900})
        profile = dict(self.p.devices[profiles[task.device]]) if task.device in profiles else {"viewport": desktop_viewport}
        profile.pop("default_browser_type", None)
        profile.update(locale="ru-RU", timezone_id="Europe/Moscow")
        if service == "images":
            # Preserve the server document and its native application startup.
            # Serializing an initialized DOM breaks BEM routing on reopening.
            ua = profile.get("user_agent") or self.p.devices["Desktop Chrome"]["user_agent"]
            def fetch_page(page_url):
                response = requests.get(page_url, headers={"User-Agent": ua, "Accept-Language": "ru-RU,ru;q=0.9"},
                                        timeout=(15, int(self.config.get("timeout_seconds", 60))))
                response.raise_for_status()
                requested = parse_qs(urlsplit(page_url).query, keep_blank_values=True)
                final = parse_qs(urlsplit(response.url).query, keep_blank_values=True)
                for key, values in requested.items():
                    if final.get(key) != values:
                        raise ValueError(f'Redirect dropped or changed requested parameter: {key}')
                page_soup, page_items = parse(response.text, service, response.url)
                return page_soup, page_items, response.url

            minimum = self.config.get('desktop_min_items', 90)
            max_pages = self.config.get('desktop_max_pages', 5)
            if task.device == 'DESKTOP':
                if type(minimum) is not int or minimum <= 0 or type(max_pages) is not int or max_pages <= 0:
                    raise ValueError('desktop_min_items and desktop_max_pages must be positive integers')
            soup, items, captured_url = fetch_page(url)
            info = {"requested_url": url, "captured_url": captured_url,
                    "viewport": profile["viewport"], "mode": "original-html"}
            if task.device == 'DESKTOP':
                soup, items, pagination = collect_desktop(soup, items, captured_url, fetch_page, minimum, max_pages)
                info.update(pagination)
            return soup, items, info
        context = self.browser.new_context(**profile)
        page = context.new_page()
        timeout = int(self.config.get("timeout_seconds", 60))
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            if response is None or response.status >= 400:
                raise ValueError(f"Search returned HTTP {response.status if response else 'no response'}")
            selector = ".SerpItem a img, .serp-item a img" if service == "images" else ".VideoSnippet .VideoSnippet-Title"
            try:
                page.wait_for_selector(selector, state="attached", timeout=timeout * 1000)
            except BrowserTimeout:
                raise ParseError("No rendered result cards. CAPTCHA, empty result or unsupported layout; no page published") from None
            # Wait until layout and structured data agree, rather than a fixed screenshot delay.
            deadline, last_error = time.monotonic() + timeout, None
            while time.monotonic() < deadline:
                try:
                    html = page.content()
                    soup, items = parse(html, service, page.url)
                    if service == "images" and len(soup.select(".SerpItem, .serp-item")) < len(items):
                        raise ParseError("Result cards still rendering")
                    break
                except ParseError as error:
                    last_error = error
                    page.wait_for_timeout(250)
            else:
                raise last_error or ParseError("Result hydration timed out")
            page.wait_for_timeout(int(self.config.get("settle_ms", 800)))
            html = page.content()
            soup, items = parse(html, service, page.url)
            final_params = parse_qs(urlsplit(page.url).query)
            if final_params.get("text", [""])[0] != task.query or final_params.get("lr", [""])[0] != str(task.region_id):
                raise ValueError("Redirect changed query or region_id")
            ids = self.config.get("test_ids", [])
            if ids and final_params.get(self.config.get("test_id_param", "test-id"), [""])[0] != ",".join(map(str, ids)):
                raise ValueError("Redirect dropped or changed requested test IDs")
            return soup, items, {"requested_url": url, "captured_url": page.url,
                                 "viewport": profile["viewport"], "browser": "chromium"}
        finally:
            context.close()


# ========================================================================
# render
# ========================================================================

class Assets:
    def __init__(self):
        self.cache = {}

    def fetch(self, url):
        if url in self.cache:
            return self.cache[url]
        with requests.get(url, timeout=(10, 25), stream=True) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 12 * 1024 * 1024:
                    raise ValueError("Asset exceeds 12 MiB")
                chunks.append(chunk)
            data = b"".join(chunks)
            mime = response.headers.get("Content-Type", "").split(";")[0]
        self.cache[url] = (data, mime)
        return data, mime

    def image(self, url):
        data, mime = self.fetch(url)
        if not mime.startswith("image/") or mime == "image/svg+xml":
            raise ValueError("Expected a raster image")
        return "data:" + mime + ";base64," + base64.b64encode(data).decode()

    def css(self, url):
        data, mime = self.fetch(url)
        if mime not in {"text/css", "text/plain", "application/octet-stream"}:
            raise ValueError("Expected CSS")
        text = data.decode("utf-8")
        return absolute_css(text, url)


def absolute_css(text, base):
    def replace(m):
        value = m.group(1).strip(" \t\r\n\"'")
        if value.startswith(("data:", "#")):
            return m.group(0)
        return 'url("' + urljoin(base, value).replace('"', '%22') + '")'
    # Remove source blur before embedding, including inline !important rules
    # and CSS variables that might otherwise beat the mandatory override.
    text = re.sub(r"\bblur\([^()]*\)", "blur(0)", text, flags=re.IGNORECASE)
    return re.sub(r"url\(([^)]+)\)", replace, text)


def clear_blur_flags(value):
    """Disable image censorship in every saved native state, without a toggle."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"censored", "censoredShock", "censored_shock", "isCensored",
                       "blurred", "isBlurred"}:
                value[key] = False
            elif key == "images_shock_blur":
                value[key] = "0" if isinstance(child, str) else 0
            else:
                clear_blur_flags(child)
    elif isinstance(value, list):
        for child in value:
            clear_blur_flags(child)


def render(soup, items, task, service, base, assets):
    if not soup.html or not soup.head or not soup.body:
        raise ParseError("Source is not a full HTML document")
    if service == "images":
        return render_native(soup, items, task, base, assets)
    bound = []
    cards = soup.select(".VideoSnippet")
    if len(cards) != len(items):
        raise ParseError("Video record/card count differs")
    for card, item in zip(cards, items):
        links = card.select(".VideoSnippet-Thumb, .VideoSnippet-Title, .VideoHostExtended-Host")
        if len(links) < 2:
            raise ParseError("Video card lacks clickable title and thumbnail")
        for a in links:
            a.name = "a"
            a["href"] = item.page_url
            a["target"] = "_blank"
            a["rel"] = "noopener noreferrer"
            a["tabindex"] = "0"
            a.attrs.pop("aria-hidden", None)
            a["data-sbs-video"] = str(item.position)
        poster = card.select_one("video, img")
        img = soup.new_tag("img", src=item.thumbnail_url, alt=item.title)
        img["class"] = poster.get("class", [])
        img["style"] = "width:100%;height:100%;object-fit:cover"
        poster.replace_with(img)
        bound.extend(links)

    # Remove original app runtime and tracking: video links point directly to their source.
    for node in soup.select("script, iframe, object, embed, noframes, base, link[rel=preload], link[rel=modulepreload], link[rel=prefetch], .YndxBug, #yndxbug, .TechTool, .DevToolsPanel, .MessageBox_layout_tooltip, [role=tooltip]"):
        node.decompose()
    for meta in soup.select("meta[http-equiv]"):
        meta.decompose()
    for node in soup.find_all(True):
        for attr in list(node.attrs):
            if attr.lower().startswith("on") or (attr.startswith("data-") and not attr.startswith("data-sbs-")) or attr in {"nonce", "integrity", "srcset", "ping"}:
                del node[attr]
        if node.get("style"):
            node["style"] = absolute_css(node["style"], base)
        for attr in ("href", "src", "action", "poster"):
            value = node.get(attr)
            if not value or value.startswith(("#", "data:image/")):
                continue
            safe = http_url(value, base)
            if safe:
                node[attr] = safe
            else:
                node.attrs.pop(attr, None)
        if node.name == "a" and node.get("href", "").startswith(("https:", "http:")):
            node["target"], node["rel"] = "_blank", "noopener noreferrer"
    for style in soup.select("style"):
        style.string = absolute_css(style.get_text(), base)
    links = soup.select('link[rel="stylesheet"][href]')
    with ThreadPoolExecutor(max_workers=5) as pool:
        css = list(pool.map(assets.css, [x["href"] for x in links]))
    for link, text in zip(links, css):
        style = soup.new_tag("style")
        style.string = text
        link.replace_with(style)

    # Cache the displayed images in the HTML: no expiring thumbnail links on S3.
    images = [img for img in soup.select("img[src]") if not img["src"].startswith("data:")]
    urls = list(dict.fromkeys(img["src"] for img in images))
    with ThreadPoolExecutor(max_workers=6) as pool:
        encoded = dict(zip(urls, pool.map(assets.image, urls)))
    for img in images:
        img["src"] = encoded[img["src"]]

    soup.html['data-sbs-unblur'] = 'true'
    own_style = soup.new_tag("style")
    own_style.string = VIDEO_CSS
    soup.head.append(own_style)
    banner = soup.new_tag("div", id="sbs-banner")
    banner.string = f"Сохранённая выдача · {task.query} · регион {task.region_id} · {task.device}"
    soup.body.insert(0, banner)
    referrer = soup.new_tag("meta", attrs={"name": "referrer", "content": "no-referrer"})
    soup.head.insert(0, referrer)
    csp = ("default-src 'none'; img-src data: https: http:; style-src 'unsafe-inline' https:; "
           "script-src 'none'; font-src data: https:; connect-src 'none'; "
           "base-uri 'none'; form-action 'none'; object-src 'none'")
    soup.head.insert(0, soup.new_tag("meta", attrs={"http-equiv": "Content-Security-Policy", "content": csp}))
    return str(soup), {"items": len(items), "source_links": len(items), "bound_links": len(bound),
                       "embedded_images": len(images), "embedded_stylesheets": len(links),
                       "censored_items": sum(x.censored for x in items),
                       "source_blur_preserved": False, "remove_blur": True}


# ========================================================================
# native
# ========================================================================

def render_native(soup, items, task, base, assets):

    root = soup.select_one('[id^="ImagesApp"][data-state]')
    if root is None or not soup.select('script[data-as="webpack-runtime"][src]'):
        raise ParseError("Native image viewer runtime is missing; capture the original HTML again")
    state = json.loads(root['data-state'])
    records = state['initialState']['serpList']['items']['entities']
    # Match yandex old: keep native thumbnail and original URLs. The browser
    # loads them when displaying the saved page; export never fetches them.
    for item in items:
        record = records[item.id]
        viewer = record.get('viewerData', {})
        record['censored'] = False
        viewer['censored'] = False
    clear_blur_flags(state)
    # Source JSON may contain an unpaired UTF-16 surrogate. Keep it escaped
    # so the HTML remains valid UTF-8 and JSON.parse receives the same value.
    root['data-state'] = json.dumps(state, ensure_ascii=True, separators=(',', ':'))

    # Do not strip application state, native scripts, event handlers or image
    # links: these are required for the production viewer and its information.
    for node in soup.select('base, meta[http-equiv], iframe, #init-metrika, #aab-d'):
        node.decompose()
    for node in soup.find_all(True):
        # BEM nodes and other native roots can carry their own censor flags.
        for attr in ('data-state', 'data-bem'):
            if attr not in node.attrs:
                continue
            try:
                native_data = json.loads(node[attr])
            except (ValueError, TypeError):
                continue
            clear_blur_flags(native_data)
            node[attr] = json.dumps(native_data, ensure_ascii=True, separators=(',', ':'))
        if node.get('class'):
            node['class'] = [c for c in node['class'] if not c.endswith('_js_inited')]
        for attr in ('src', 'href', 'action', 'poster'):
            value = node.get(attr)
            if value and not value.startswith(('data:', '#', 'javascript:')):
                node[attr] = urljoin(base, value)
        if node.get('style'):
            node['style'] = absolute_css(node['style'], base)
    for script in soup.select('script[src]'):
        if any(x in script['src'] for x in ('/ads/', 'mc.yandex.', '/ulitochka/', '/safeframe-')):
            script.decompose()
    for script in soup.select('script:not([src])'):
        if '/ulitochka/' in script.get_text() or 'butterfly/v1/' in script.get_text():
            script.decompose()
    for style in soup.select('style'):
        style.string = absolute_css(style.get_text(), base)
    links = soup.select('link[rel="stylesheet"][href]')
    # Keep stylesheet links just like the reference; no asset download here.

    soup.html['data-sbs-device'] = task.device
    soup.html['data-sbs-source'] = base
    soup.html['data-sbs-unblur'] = 'true'
    style = soup.new_tag('style', id='sbs-native-style')
    style.string = NATIVE_CSS
    soup.head.append(style)
    script = soup.new_tag('script', id='sbs-native-adapter')
    script.string = NATIVE_JS
    soup.head.insert(0, script)
    soup.head.insert(0, soup.new_tag('meta', attrs={'name': 'referrer', 'content': 'no-referrer'}))
    return str(soup), {'items': len(items), 'source_links': len(items),
                      'embedded_images': 0, 'embedded_stylesheets': 0,
                      'linked_images': len(items), 'linked_stylesheets': len(links),
                      'image_loading': 'browser',
                      'viewer': 'yandex-native', 'censored_items': sum(i.censored for i in items),
                      'source_blur_preserved': False, 'remove_blur': True}


# ========================================================================
# storage
# ========================================================================

class Storage:
    def __init__(self, config):
        self.config = config
        self.bucket = config["bucket"]
        self.prefix = config.get("prefix", "sbs-capture").strip("/")
        if not self.prefix or ".." in self.prefix.split("/"):
            raise ValueError("A nonempty, safe S3 prefix is required")
        self.endpoint = config["endpoint"].rstrip("/")
        # Credentials come only from standard AWS environment/profile providers.
        self.client = boto3.client("s3", endpoint_url=self.endpoint,
            region_name=config.get("region", "ru-1"),
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                          retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=15, read_timeout=30))

    def publish(self, key, html):
        full_key = f"{self.prefix}/{key}"
        body = html.encode("utf-8")
        args = dict(Bucket=self.bucket, Key=full_key, Body=body,
                    ContentType="text/html; charset=utf-8", ContentDisposition="inline",
                    CacheControl="public, max-age=31536000, immutable",
                    Metadata={"sha256": hashlib.sha256(body).hexdigest()})
        if self.config.get("acl"):
            args["ACL"] = self.config["acl"]
        self.client.put_object(**args)
        public_base = self.config.get("public_base_url", f"{self.endpoint}/{self.bucket}").rstrip("/")
        url = public_base + "/" + quote(full_key, safe="/")
        # Do not report a URL as successful until anonymous readback matches.
        response = requests.get(url, timeout=(15, 45))
        response.raise_for_status()
        if response.content != body or "text/html" not in response.headers.get("Content-Type", ""):
            raise ValueError("S3 readback does not match uploaded HTML")
        return url


# ========================================================================
# CLI
# ========================================================================

def safe_error(error):
    text = str(error)
    for key, value in os.environ.items():
        if value and any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            text = text.replace(value, "[redacted]")
    return text[:1000]


def main():
    ap = argparse.ArgumentParser(description="Скачать выдачу и опубликовать кликабельные HTML в S3")
    ap.add_argument("--input", required=True, help="JSON object, array or JSONL with query/region_id/device")
    ap.add_argument("--config", help="JSON configuration; built-in image settings by default")
    ap.add_argument("--service", choices=["images", "video"], help="Override service from config")
    ap.add_argument("--output", default="runs", help="Local run directory")
    ap.add_argument("--local-only", action="store_true", help="Skip upload, return local paths")
    ap.add_argument("--html", help="Repair a saved full HTML document; one input query only")
    ap.add_argument("--source-url", help="Original search URL for --html")
    args = ap.parse_args()
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8")) if args.config else json.loads(json.dumps(DEFAULT_CONFIG))
        if not isinstance(config, dict):
            raise ValueError("Config must be a JSON object")
        # Mandatory for every export, including old configs with false.
        config["remove_blur"] = True
        if args.service:
            config["service"] = args.service
        text = Path(args.input).read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        tasks = [Query.load(x) for x in (data if isinstance(data, list) else [data])]
        if not tasks:
            raise ValueError("Input is empty")
        if args.html and (len(tasks) != 1 or not args.source_url):
            raise ValueError("--html needs exactly one query and --source-url")
        if config.get("service", "images") not in {"images", "video"}:
            raise ValueError("service must be images or video")
        storage = None
        if not args.local_only:
            storage = Storage(config["s3"])
            if storage.client._request_signer._credentials is None:
                raise ValueError("Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY or AWS_PROFILE")
    except Exception as error:
        print(safe_error(error), file=sys.stderr)
        return 2
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = Path(args.output).resolve() / run_id
    output.mkdir(parents=True, exist_ok=False)
    if storage:
        print('Режим: публикация HTML в S3; публичные ссылки будут в строках HTML: и urls.txt.',
              file=sys.stderr, flush=True)
    else:
        print('Режим: локальное сохранение (--local-only); публичные ссылки не создаются.',
              file=sys.stderr, flush=True)
    service = config.get("service", "images")
    results, assets = [], Assets()
    capture, playwright = None, None
    try:
        if not args.html:
            from playwright.sync_api import sync_playwright
            playwright = sync_playwright().start()
            capture = Capture(playwright, config)
        for index, task in enumerate(tasks, 1):
            result = {"input_index": index - 1, **task.as_dict(), "service": service, "status": "error"}
            try:
                if args.html:
                    soup, items = parse(Path(args.html).read_text(encoding="utf-8"), service, args.source_url)
                    info = {"captured_url": args.source_url, "mode": "repair"}
                else:
                    soup, items, info = capture.run(task)
                html, audit = render(soup, items, task, service, info["captured_url"], assets)
                digest = hashlib.sha256(json.dumps(task.as_dict(), sort_keys=True).encode()).hexdigest()[:12]
                name = f"{index:06d}-{digest}"
                path = output / f"{name}.html"
                path.write_text(html, encoding="utf-8")
                (output / f"{name}.json").write_text(json.dumps({"request": task.as_dict(), "capture": info,
                    "audit": audit, "items": [asdict(x) for x in items]}, ensure_ascii=True, indent=2), encoding="utf-8")
                result.update(audit=audit, html_path=str(path))
                if storage:
                    result["s3_url"] = storage.publish(f"{run_id}/{service}/{name}.html", html)
                    print(f'HTML: {result["s3_url"]}', file=sys.stderr, flush=True)
                result["status"] = "ok"
            except Exception as error:
                result["error"] = safe_error(error)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            if storage:
                (output / 'urls.txt').write_text(
                    ''.join(r['s3_url'] + '\n' for r in results if r.get('s3_url')),
                    encoding='utf-8')
    except Exception as error:
        print(safe_error(error), file=sys.stderr)
        return 2
    finally:
        if capture:
            capture.close()
        if playwright:
            playwright.stop()
    print(f"Results: {output / 'results.json'}", file=sys.stderr)
    if storage:
        print(f"HTML links: {output / 'urls.txt'}", file=sys.stderr)
    return 1 if any(r["status"] != "ok" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
