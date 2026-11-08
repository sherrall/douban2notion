import argparse
from email import feedparser
import json
import os
import re
import pendulum
from retrying import retry
import requests
from douban2notion.notion_helper import NotionHelper
from douban2notion import utils
DOUBAN_API_HOST = os.getenv("DOUBAN_API_HOST", "frodo.douban.com")
DOUBAN_API_KEY = os.getenv("DOUBAN_API_KEY", "0ac44ae016490db2204ce0a042db2916")

from douban2notion.config import movie_properties_type_dict,book_properties_type_dict, TAG_ICON_URL, USER_ICON_URL
from douban2notion.utils import get_icon
from dotenv import load_dotenv
load_dotenv()
rating = {
    1: "⭐️",
    2: "⭐️⭐️",
    3: "⭐️⭐️⭐️",
    4: "⭐️⭐️⭐️⭐️",
    5: "⭐️⭐️⭐️⭐️⭐️",
}
movie_status = {
    "mark": "想看",
    "doing": "在看",
    "done": "看过",
}
book_status = {
    "mark": "想读",
    "doing": "在读",
    "done": "读过",
}
AUTH_TOKEN = os.getenv("AUTH_TOKEN")

base_headers = {
    "host": DOUBAN_API_HOST,
    "user-agent": "User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 15_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.16(0x18001023) NetType/WIFI Language/zh_CN",
    "referer": "https://servicewechat.com/wx2f9b06c1de1ccfca/84/page-frame.html",
}


def build_headers(auth_token=None):
    """构造请求头。

    AUTH_TOKEN 必须是豆瓣 OAuth 的 access_token 才有效；
    浏览器 Cookie 里的 dbcl2（形如 "84990980:xxxx"）不是 access_token，
    传进来会被豆瓣判定 invalid_access_token 并返回 HTTP 400。
    因此这里允许传 None 走"无鉴权"通道。
    """
    headers = dict(base_headers)
    headers["authorization"] = f"Bearer {auth_token}" if auth_token else ""
    return headers


headers = build_headers(AUTH_TOKEN)


def is_invalid_token(response):
    """识别 token 无效这一类错误，便于自动降级。"""
    if response.status_code in (400, 401):
        text = response.text or ""
        return "invalid_access_token" in text or "access_token" in text
    return False


@retry(stop_max_attempt_number=3, wait_fixed=5000)
def fetch_subjects(user, type_, status):
    offset = 0
    page = 0
    url = f"https://{DOUBAN_API_HOST}/api/v2/user/{user}/interests"
    results = []
    token = AUTH_TOKEN if AUTH_TOKEN else None
    while True:
        params = {
            "type": type_,
            "count": 50,
            "status": status,
            "start": offset,
            "apiKey": DOUBAN_API_KEY,
        }
        response = requests.get(url, headers=build_headers(token), params=params, timeout=30)

        # AUTH_TOKEN 配错（例如误填 dbcl2）时自动降级为无鉴权重试一次，
        # 避免把本来能跑通的公开数据同步直接跑挂。
        if token and is_invalid_token(response):
            print(
                "⚠️  AUTH_TOKEN 被豆瓣判定为无效（{code}），已自动降级为无鉴权模式重试。\n"
                "    若长期如此，请检查该 Secret 是否为 OAuth access_token；"
                "豆瓣公开的想读/在读/读过数据通常无需 token 即可拉取。".format(
                    code=response.status_code
                )
            )
            token = None
            response = requests.get(url, headers=build_headers(None), params=params, timeout=30)

        if not response.ok:
            # 旧代码在这里静默跳过，导致 while True 死循环、任务挂到超时。
            # 现在直接抛出可读错误，让 Actions 快速失败并给出排查指引。
            raise RuntimeError(
                "豆瓣接口请求失败：HTTP {code}\n"
                "  body   = {body}\n"
                "  user   = {user}\n"
                "  type   = {type}\n"
                "  status = {status}\n"
                "  start  = {start}\n"
                "常见原因：\n"
                "  1) DOUBAN_NAME 不是有效的豆瓣用户 ID/数字 UID（去个人主页地址栏确认，"
                "形如 https://www.douban.com/people/84990980/ 时填 84990980）；\n"
                "  2) AUTH_TOKEN 填错（必须是 OAuth access_token，不能是浏览器 Cookie 的 dbcl2）；\n"
                "  3) 账号被豆瓣限制访问或触发了风控频率限制。".format(
                    code=response.status_code,
                    body=response.text[:300],
                    user=user,
                    type=type_,
                    status=status,
                    start=offset,
                )
            )

        data = response.json()
        interests = data.get("interests")
        if interests is None:
            raise RuntimeError(
                "豆瓣接口响应缺少 interests 字段，无法继续：{body}".format(
                    body=str(data)[:300]
                )
            )
        if len(interests) == 0:
            break
        results.extend(interests)
        print(f"total = {len(results)}")
        page += 1
        offset = page * 50
    return results



def insert_movie(douban_name,notion_helper):
    notion_movies = notion_helper.query_all(database_id=notion_helper.movie_database_id)
    notion_movie_dict = {}
    for i in notion_movies:
        movie = {}
        for key, value in i.get("properties").items():
            movie[key] = utils.get_property_value(value)
        notion_movie_dict[movie.get("豆瓣链接")] = {
            "短评": movie.get("短评"),
            "状态": movie.get("状态"),
            "日期": movie.get("日期"),
            "评分": movie.get("评分"),
            "page_id": i.get("id")
        }
    print(f"notion {len(notion_movie_dict)}")
    results = []
    for i in movie_status.keys():
        results.extend(fetch_subjects(douban_name, "movie", i))
    for result in results:
        movie = {}
        if not result:
            print(result)
            continue
        subject = result.get("subject")
        if not subject:
            print("跳过缺少 subject 的记录：{r}".format(r=str(result)[:200]))
            continue
        movie["电影名"] = subject.get("title")
        create_time = result.get("create_time")
        create_time = pendulum.parse(create_time,tz=utils.tz)
        #时间上传到Notion会丢掉秒的信息，这里直接将秒设置为0
        create_time = create_time.replace(second=0)
        movie["日期"] = create_time.int_timestamp
        movie["豆瓣链接"] = subject.get("url")
        movie["状态"] = movie_status.get(result.get("status"))
        if result.get("rating"):
            movie["评分"] = rating.get(result.get("rating").get("value"))
        if result.get("comment"):
            movie["短评"] = result.get("comment")
        if notion_movie_dict.get(movie.get("豆瓣链接")):
            notion_movive = notion_movie_dict.get(movie.get("豆瓣链接"))
            if (
                notion_movive.get("日期") != movie.get("日期")
                or notion_movive.get("短评") != movie.get("短评")
                or notion_movive.get("状态") != movie.get("状态")
                or notion_movive.get("评分") != movie.get("评分")
            ):
                properties = utils.get_properties(movie, movie_properties_type_dict)
                notion_helper.get_date_relation(properties,create_time)
                notion_helper.update_page(
                    page_id=notion_movive.get("page_id"),
                    properties=properties
            )

        else:
            print(f"插入{movie.get('电影名')}")
            pic = subject.get("pic") or {}
            cover = pic.get("normal")
            if cover and not cover.endswith('.webp'):
                cover = cover.rsplit('.', 1)[0] + '.webp'
            if cover:
                movie["封面"] = cover
            movie["类型"] = subject.get("type")
            if subject.get("genres"):
                movie["分类"] = [
                    notion_helper.get_relation_id(
                        x, notion_helper.category_database_id, TAG_ICON_URL
                    )
                    for x in subject.get("genres")
                ]
            if subject.get("actors"):
                l = []
                actors = subject.get("actors")[0:100]
                for actor in actors:
                    if actor.get("name"):
                        if "/" in actor.get("name"):
                            l.extend(actor.get("name").split("/"))
                        else:
                            l.append(actor.get("name"))
                l = [x.strip() for x in l if x and x.strip()]
                actor_ids = [
                    notion_helper.get_relation_id(
                        x, notion_helper.actor_database_id, USER_ICON_URL
                    )
                    for x in l[0:5]
                ]
                actor_ids = [x for x in actor_ids if x]
                if actor_ids:
                    movie["演员"] = actor_ids
            if subject.get("directors"):
                movie["导演"] = [
                    notion_helper.get_relation_id(
                        x.get("name"), notion_helper.director_database_id, USER_ICON_URL
                    )
                    for x in subject.get("directors")[0:100]
                ]
            properties = utils.get_properties(movie, movie_properties_type_dict)
            notion_helper.get_date_relation(properties,create_time)
            parent = {
                "database_id": notion_helper.movie_database_id,
                "type": "database_id",
            }
            notion_helper.create_page(
                parent=parent, properties=properties, icon=get_icon(cover)
            )


def insert_book(douban_name,notion_helper):
    notion_books = notion_helper.query_all(database_id=notion_helper.book_database_id)
    notion_book_dict = {}
    for i in notion_books:
        book = {}
        for key, value in i.get("properties").items():
            book[key] = utils.get_property_value(value)
        notion_book_dict[book.get("豆瓣链接")] = {
            "短评": book.get("短评"),
            "状态": book.get("状态"),
            "日期": book.get("日期"),
            "评分": book.get("评分"),
            "page_id": i.get("id")
        }
    print(f"notion {len(notion_book_dict)}")
    results = []
    for i in book_status.keys():
        results.extend(fetch_subjects(douban_name, "book", i))
    for result in results:
        book = {}
        if not result:
            print(result)
            continue
        subject = result.get("subject")
        if not subject:
            print("跳过缺少 subject 的记录：{r}".format(r=str(result)[:200]))
            continue
        book["书名"] = subject.get("title")
        create_time = result.get("create_time")
        create_time = pendulum.parse(create_time,tz=utils.tz)
        #时间上传到Notion会丢掉秒的信息，这里直接将秒设置为0
        create_time = create_time.replace(second=0)
        book["日期"] = create_time.int_timestamp
        book["豆瓣链接"] = subject.get("url")
        book["状态"] = book_status.get(result.get("status"))
        if result.get("rating"):
            book["评分"] = rating.get(result.get("rating").get("value"))
        if result.get("comment"):
            book["短评"] = result.get("comment")
        if notion_book_dict.get(book.get("豆瓣链接")):
            notion_movive = notion_book_dict.get(book.get("豆瓣链接"))
            if (
                notion_movive.get("日期") != book.get("日期")
                or notion_movive.get("短评") != book.get("短评")
                or notion_movive.get("状态") != book.get("状态")
                or notion_movive.get("评分") != book.get("评分")
            ):
                properties = utils.get_properties(book, book_properties_type_dict)
                notion_helper.get_date_relation(properties,create_time)
                notion_helper.update_page(
                    page_id=notion_movive.get("page_id"),
                    properties=properties
            )

        else:
            print(f"插入{book.get('书名')}")
            pic = subject.get("pic") or {}
            cover = pic.get("large")
            if cover:
                book["封面"] = cover
            book["简介"] = subject.get("intro")
            press = []
            for i in subject.get("press") or []:
                press.extend(i.split(","))
            book["出版社"] = press
            book["类型"] = subject.get("type")
            if result.get("tags"):
                book["分类"] = [
                    notion_helper.get_relation_id(
                        x, notion_helper.category_database_id, TAG_ICON_URL
                    )
                    for x in result.get("tags")
                ]
            if subject.get("author"):
                book["作者"] = [
                    notion_helper.get_relation_id(
                        x, notion_helper.author_database_id, USER_ICON_URL
                    )
                    for x in subject.get("author")[0:100]
                ]
            properties = utils.get_properties(book, book_properties_type_dict)
            notion_helper.get_date_relation(properties,create_time)
            parent = {
                "database_id": notion_helper.book_database_id,
                "type": "database_id",
            }
            notion_helper.create_page(
                parent=parent, properties=properties, icon=get_icon(cover)
            )

     
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("type")
    options = parser.parse_args()
    type = options.type
    notion_helper = NotionHelper(type)
    is_movie = True if type=="movie" else False
    douban_name = os.getenv("DOUBAN_NAME", None)
    if not douban_name:
        raise RuntimeError("未设置 DOUBAN_NAME，请在仓库 Secrets 中配置你的豆瓣用户 ID。")
    if not AUTH_TOKEN:
        print("提示：未配置 AUTH_TOKEN，将以无鉴权模式访问豆瓣（公开的想看/在读/读过数据可正常拉取）。")
    if is_movie:
        insert_movie(douban_name,notion_helper)
    else:
        insert_book(douban_name,notion_helper)
if __name__ == "__main__":
    main()