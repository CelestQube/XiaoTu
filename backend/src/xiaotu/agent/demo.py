from datetime import date
from math import floor
import re

from xiaotu.domain import trip_data, profile_data

FIXTURES = {
    "杭州": (["西湖","灵隐飞来峰","西溪湿地","京杭大运河杭州景区","良渚博物院","中国茶叶博物馆","浙江省博物馆之江馆"],["湖滨","武林广场"],["河坊街","大兜路"]),
    "成都": (["武侯祠","成都博物馆","杜甫草堂","成都大熊猫繁育研究基地","金沙遗址博物馆","望江楼公园","宽窄巷子"],["春熙路","天府广场"],["玉林路","奎星楼街"]),
    "厦门": (["鼓浪屿","厦门园林植物园","环岛路","厦门博物馆","厦门海湾公园","集美学村","沙坡尾"],["中山路","厦门站"],["八市","中山路"]),
    "北京": (["故宫博物院","天坛公园","颐和园","中国国家博物馆","景山公园","北京动物园","首都博物馆"],["东直门","前门"],["护国寺街","前门大街"]),
}
KNOWN = ["哈尔滨","张家界","重庆","上海","广州","深圳","南京","苏州","三亚","昆明","丽江","大理","青岛","西安","长沙","武汉","洛阳",
         "济南","天津","宁波","福州","珠海","黄山","桂林",*FIXTURES]
NUM = {**dict(zip("一二三四五六七八九十", range(1, 11))), "两": 2}


def interests(text):
    return [x for x in "美食 风景 观光 摄影 亲子 骑行 登山 博物馆 海边 徒步 人文 自然".split() if x in text]


def preferences(text):
    patterns = {
        "homeCity": r"(?:常住|长期住在|长期居住在|常居)([\u4e00-\u9fa5]{2,8}?)(?:市|[，。,\s]|$)",
        "transport": r"(?:平时|通常|出游一般|习惯)[^，。,\n]{0,8}?(高铁|火车|飞机|自驾|公共交通|地铁|骑行)",
        "dailyBudget": r"(?:平时|通常|一般)[^，。,\n]{0,18}?(?:每人每天|每天|日均|日均预算)\s*(\d{2,6})\s*(?:元|块)",
    }
    result = {key: int(m[1]) if key == "dailyBudget" else m[1] for key, pattern in patterns.items() if (m := re.search(pattern, text))}
    if re.search(r"(?:旅游|旅行|出游|平时).{0,8}(?:喜欢|偏好)|我(?:很)?喜欢", text):
        result["interests"] = interests(text)
    return profile_data(result)


def hints(text, previous=None):
    result = dict(previous or {})
    origin = next((x for x in KNOWN if re.search("从" + x + r"(?:市|出发|[到去，,])", text)), "")
    if not origin and (m := re.search(r"从([\u4e00-\u9fa5]{2,8}?)出发", text)):
        origin = m[1]
    if origin:
        result["origin"] = origin
    city = next((x for x in KNOWN if re.search(r"(?:去|到|玩|换成|改成|改为|在|安排|规划|推荐|查一下)\s*" + x, text)), "")
    if not city and not re.search(r"常住|常居|长期住", text):
        city = next((x for x in KNOWN if x != origin and x in text), "")
    if not city and (m := re.search(r"(?:想去|改去|准备去|出发去|前往|去)([\u4e00-\u9fa5]{2,8}?)(?:市|玩|旅游|旅行|出游|[，。,\s]|$)", text)):
        city = m[1]
    if city and not city.endswith("出发"):
        result["city"] = city
    for key, pattern in (("days", r"(?<![第\d])([1-9]\d?|[一两二三四五六七八九十])\s*(?:天|日游)"),
                         ("people", r"([1-9]\d?|[一两二三四五六七八九十])\s*(?:个人|人|位)")):
        if m := re.search(pattern, text):
            result[key] = int(m[1]) if m[1].isdigit() else NUM[m[1]]
    if m := re.search(r"(总预算|预算|控制在|人均)\s*[为是约：:¥￥ ]*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,10}(?:\.[0-9]{1,5})?)\s*(万|千|元|块)?", text):
        value = float(m[2].replace(",", "")) * {"万":10000,"千":1000}.get(m[3], 1)
        result["budget"] = floor(value * ((result.get("people") or 1) if m[1] == "人均" else 1) + .5)
    if m := re.search(r"20\d\d[-/]\d{1,2}[-/]\d{1,2}|20\d\d年\d{1,2}月\d{1,2}日", text):
        result["startDate"] = "-".join(s.zfill(2) for s in re.findall(r"\d+", m[0]))
    if matched := interests(text):
        result["interests"] = matched
    return trip_data(result)


def places(city):
    return [dict(name=x, type=t, address=city + " · 示意位置", source="本地演示")
            for t, entries in zip(("景点","住宿商圈","餐饮商圈"), FIXTURES.get(city, ())) for x in entries]


def draft(trip, message):
    city, count = trip["city"], trip["days"]
    sites, lodging, meals = FIXTURES.get(city, ([city+"当地景点（待选）"], [city+"市区住宿区（待选）"], ["当天活动附近（待选）"]))
    which = re.search(r"第([1-7一两二三四五六七])天", message)
    idx = (int(which[1]) if which[1].isdigit() else NUM[which[1]]) if which else 0
    days = []
    for i in range(count):
        p = sites[i % len(sites)]
        indoor = re.search(r"室内|下雨|雨天", message) and idx in {0,i+1}
        venue = next((p for p in sites if "博物馆" in p), city+"室内场馆（待选）") if indoor else p
        days.append(dict(day=i+1, items=[
            dict(time="09:00", title="抵达与熟悉周边" if i == 0 else p, location=lodging[0] if i == 0 else p,
                 note="请根据实际交通调整。"),
            dict(time="12:00", title="午餐", location=meals[i % len(meals)], note="以实际营业和菜单为准。"),
            dict(time="14:00", title="室内备选：城市展馆" if indoor else p, location=venue, note="请先核对开放和预约。")]))
    return dict(title=f"{city} {count}日旅行草案", days=days, notes=["当前为示范规划。", "日期和费用均需出发前复核。"])
