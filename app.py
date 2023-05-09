#!/usr/bin/env python3

import http
import re
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import asyncio
from aiohttp import ClientSession
from bs4 import BeautifulSoup, Tag
from jinja2 import Environment, FileSystemLoader, Template


BASE_URL = 'https://t.me/s/'
TITLE_LENGTH = 80

class Feed(NamedTuple):
    """RSS feed."""

    title: str
    description: str
    pub_date: str
    link: str
    author: str


class Rss(NamedTuple):
    """RSS headers."""

    title: str
    description: str
    link: str
    last_build_date: str
    feeds: list[Feed]


@cache
def get_template() -> Template:
    """Load template from file."""
    template_loader = FileSystemLoader(searchpath=Path(__file__).parent)
    template_env = Environment(  # noqa: S701, content is trusted
        loader=template_loader,
    )
    return template_env.get_template('rss.j2')


async def get_channel_content(url: str) -> str:
    """Get HTML content of channel."""
    async with ClientSession() as session:
        async with session.get(url) as response:
            if response.status != http.HTTPStatus.OK:
                raise RuntimeError
            return await response.text()


def parse_content(raw_news: str) -> BeautifulSoup:
    """Parse raw html content to BeautifulSoup object."""
    return BeautifulSoup(raw_news, 'html.parser')


def parse_rss(soup: Tag, url: str, feeds: list[Feed]) -> Rss:
    """Extract title, description, link from root tag."""
    return Rss(
        title=soup.find('meta', {'property': 'og:title'})['content'],
        description=soup.find('meta', {'property': 'og:description'})['content'],
        link=url.replace('t.me/s/', 't.me/'),
        last_build_date=datetime.now(tz=ZoneInfo('UTC')).strftime('%a, %d %b %Y %H:%M:%S %z'),
        feeds=feeds,
    )


def parse_reply(reply: Tag) -> str:
    """Parse reply from reply tag."""
    author = reply.find('span', {'class': 'tgme_widget_message_author_name'}).decode_contents()
    text = reply.find('div', {'class': 'js-message_reply_text'}).decode_contents()

    return f"""<div class="rsshub-quote">
        <blockquote>
            <p><a href="{reply["href"]}"><b>{author}</b>:</a></p>
            <p>{text}</p>
        </blockquote>
    </div>"""


def parse_image(image: Tag) -> str:
    """Parse image from image tag."""
    image_style = image['style']
    image_link_match = re.search(r"url\('(.*)'\)", image_style)
    if not image_link_match:
        return ''

    image_link = image_link_match.group(1)
    return f'<img src="{image_link}" referrerpolicy="no-referrer">'


def parse_preview(preview: Tag) -> str:
    """Parse preview of inline link."""
    site_name = preview.find('div', {'class': 'link_preview_site_name'}).decode_contents()

    image = preview.find('i', {'class': 'link_preview_image'})
    image_text = ''
    if image:
        image_text = parse_image(image)

    preview_title = preview.find('div', {'class': 'link_preview_title'})
    preview_title_text = '<a href="{href}">{title}</a>'.format(
        href=preview['href'],
        title=preview_title.decode_contents() if preview_title else site_name,
    )

    preview_description = preview.find(
        'div', {'class': 'link_preview_description'},
    )
    preview_description_text = ''

    if preview_description:
        preview_description_text = '<p>{description}</p>'.format(
            description=preview_description.decode_contents(),
        )

    return f"""<blockquote>
        <b>{site_name}</b><br>
        <b>{preview_title_text}</b><br>
        {preview_description_text}
        {image_text}
    </blockquote>"""


def parse_feed(feed_tag: Tag) -> Feed:
    """Parse feed from message tag."""
    text_content = feed_tag.find('div', {'class': 'js-message_text'})
    description = ''
    if text_content:
        text_content_inner = text_content.find('div', {'class': 'js-message_text'})
        if text_content_inner:
            description = text_content_inner.decode_contents()
        else:
            description = text_content.decode_contents()

    title = description[:TITLE_LENGTH] or 'No title'
    images = feed_tag.find_all('a', {'class': 'tgme_widget_message_photo_wrap'})
    for image in images:
        image_text = parse_image(image)
        description = f'{description}\n{image_text}'

    reply = feed_tag.find('a', {'class': 'tgme_widget_message_reply'})
    preview = feed_tag.find('a', {'class': 'tgme_widget_message_link_preview'})

    if reply:
        description = '{reply_text}\n{description}'.format(
            reply_text=parse_reply(reply),
            description=description,
        )

    video = feed_tag.find('div', {'class': 'tgme_widget_message_video_wrap'})
    if video:
        description = (
            f'{description}'
            '\n<p><b>The message contain video, for watch it please visit the channel.</b></p>'
        )

    if preview:
        description = '{description}\n{preview_text}'.format(
            description=description,
            preview_text=parse_preview(preview),
        )

    author = feed_tag.find('span', {'class': 'tgme_widget_message_from_author'})
    if not author:
        author = feed_tag.find('a', {'class': 'tgme_widget_message_owner_name'})

    return Feed(
        title=f'{title}...',
        description=f'{description}',
        pub_date=feed_tag.find('time', {'class': 'time'})['datetime'],
        link=feed_tag.find('a', {'class': 'tgme_widget_message_date'})['href'],
        author=author.text,
    )


def parse_feeds(soup: BeautifulSoup) -> list[Feed]:
    """Parse feeds from root tag."""
    feeds: list[Feed] = []

    feed_tag: Tag
    for feed_tag in soup.find_all('div', {'class': 'tgme_widget_message_wrap'}):
        feed = parse_feed(feed_tag)
        feeds.append(feed)

    return feeds


def render_rss(rss: Rss) -> str:
    """Render rss in RSS format."""
    template = get_template()
    return template.render(rss=rss)


async def get_rss_feed(channel: str) -> str:
    """Get RSS feed from telegram channel in specified format."""
    url = urljoin(BASE_URL, channel)

    channel_content = await get_channel_content(url)
    soup = parse_content(channel_content)

    feeds = parse_feeds(soup)
    rss = parse_rss(soup, url, feeds)

    return render_rss(rss)

async def main():
    for channel in ['exp_fest']:
        rss_content = await get_rss_feed(channel)

        with open(f'gh-pages/{channel}.rdf', 'w') as f:
            f.write(rss_content)
            f.truncate()


if __name__ == "__main__":
    asyncio.run(main())
