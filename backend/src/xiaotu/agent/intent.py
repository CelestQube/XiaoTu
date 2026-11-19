"""Keep an existing itinerary as context, not as permission to change it."""
import re

from xiaotu.agent import demo
from xiaotu.domain import clean, obj


SOCIAL = re.compile(
    r"(?:你好|您好|在吗|嗨|哈喽|早上好|晚上好|早安|晚安|hi|hello|hey|谢谢(?:你|啦|了)?|感谢(?:你)?|再见|拜拜)"
    r"[呀啊哦呢哈～~\s！!？?。.…，,]*", re.I
)
GENERAL = re.compile(r"^(?:(?:请|能不能|可以|帮我)?(?:解释|科普)(?:一下)?|什么是|何谓|如何查询|怎么(?:查|买)|怎样(?:查|买))")
TRAVEL_ADVICE = re.compile(r"该带什么|需要带什么|要带什么|要注意什么|需要注意什么|有哪些注意事项|有什么好玩|几天(?:比较|才|最)合适|能做什么|怎么用|是什么|什么意思|有什么区别")
EXPLICIT_ACTION = re.compile(r"(?:请|帮我|给我|替我|能不能|可以)\s*(?:规划|安排|制定|生成|重排|修改|调整|改一下)")
NO_PLAN = re.compile(r"^(?:先)?(?:不(?:用|必|要)?|别|暂时不|暂不)(?:帮我)?(?:规划|安排|调整|修改|改动|重排)")
PLAN = re.compile(
    r"(?:安排|制定|设计|生成|规划|重排|调整|修改|更改|更新|优化|排一下|重新排)[^。！？?!]{0,18}(?:行程|旅行计划|出游计划|旅游计划|日游|每天)"
    r"|(?:行程|第[一二三四五六七两1-7]天|(?:首|末)日)[^。！？?!]{0,18}(?:改成|换成|改到|挪到|替换|删掉|取消|调整一下|放松一点|多安排|加上|加入|改一下)"
    r"|(?:加入|加进|加入到|放进|排进)[^。！？?!]{0,12}行程"
    r"|(?:^|[，,。]|(?:我)?(?:想|要|准备|打算)[^。！？?!]{0,12}|从[\u4e00-\u9fff]{2,10}|出发)去[\u4e00-\u9fff]{2,12}(?:玩|旅游|旅行|出游|[一两二三四五六七1-7](?:天|日游))"
)
GOTO = re.compile(r"^(?:我)?(?:想|准备|打算)?去(" + "|".join(demo.KNOWN) + r")(?:市)?[。！!\s]*$")
WEATHER = re.compile(r"天气|预报|气温|温度|下雨|会下雪|降温|有雨|有风|台风")
RAIL = re.compile(r"高铁|动车|火车|铁路|列车|车票|余票|换乘|经停|三字码|12306", re.I)
READ = re.compile(r"查|看看|有(?:没有|票|哪些|什么|几|高铁|动车|火车|列车)|还有|多少钱|票价|余票|车次|几点|经停|三字码|中转")
DATE_ONLY = re.compile(r"(?:就|查|看)?\s*(?:今天|明天|后天|大后天|20[0-9]{2}[-/][0-9]{1,2}[-/][0-9]{1,2})[吧呢。？！!？?\s]*")


def is_social(message):
    return SOCIAL.fullmatch(message) is not None


def clarification(history, prefix):
    """Read the most recent reply only. Older requests are not active turns."""
    if (len(history) >= 2 and history[-1]["role"] == "assistant" and history[-2]["role"] == "user"
            and history[-1]["content"].startswith(prefix)):
        return history[-2]["content"]
    return ""


def plan_request(message, history, previous, proposal=None):
    if (is_social(message) or GENERAL.search(message) or NO_PLAN.search(message)
            or TRAVEL_ADVICE.search(message) and not EXPLICIT_ACTION.search(message)):
        return False
    if PLAN.search(message) or GOTO.fullmatch(message):
        return True
    if clarification(history, "还需要："):
        hints = demo.hints(message)
        if any(k not in previous and hints.get(k) for k in ("days", "people")):
            return True
        if "city" not in previous and (message.rstrip("。！!吧") in demo.KNOWN or GOTO.fullmatch(message)):
            return True
    if strong_lookup(message):
        return False
    # Less formulaic requests may be classified by Qwen, but require the actual
    # user wording to contain an action, not a date/city taken from context.
    proposal = obj(proposal)
    evidence = clean(proposal.get("evidence"), 160)
    return bool(proposal.get("planAction") in ("create", "update") and evidence and evidence in message
                and re.search(r"规划|安排|排(?:个|一下|上)|设计|行程|攻略|计划|改成|换成|挪到|加进|延长|缩短", evidence)
                and not re.search(r"什么是|是什么|区别|怎么用|你(?:能|会|可以)|能做什么|仅(?:想|是)了解", message))


def strong_lookup(message):
    if GENERAL.search(message) or re.search(r"是什么|什么叫|有什么区别|什么意思|(?:预报|天气预报)准不准", message):
        return ""
    if WEATHER.search(message) and (re.search(r"查|怎么样|如何|几度|多少度|会不会|今天|明天|后天|这几天|未来|周[一二三四五六日天末]", message)
                                   or re.search(r"[\u4e00-\u9fff]{2,}(?:天气|气温)[。！？?！]*$", message)):
        return "weather"
    if RAIL.search(message) and READ.search(message):
        return "rail"
    return ""


def resolve_route(message, history, previous, proposal):
    """Return the chosen intent plus text relevant to a read-only lookup."""
    if is_social(message):
        return "chat", message, {}
    proposal = obj(proposal)
    guess, query = proposal.get("intent"), obj(proposal.get("query"))
    if DATE_ONLY.fullmatch(message) and (prior := clarification(history, "请补充")) and RAIL.search(prior):
        text = prior + "，" + message
        return "lookup", text, {**(query if query.get("kind") == "rail" else {}), "kind": "rail"}
    if ((prior := clarification(history, "你想查询哪个城市的天气？")) and
            (city := message.rstrip("市。！!吧")) in demo.KNOWN):
        return "lookup", prior + "，" + message, {"kind":"weather", "city":city}
    if plan_request(message, history, previous, proposal if guess == "plan" else None):
        return "plan", message, {}
    if kind := strong_lookup(message):
        if (kind == "rail" and guess == "lookup" and
                (query.get("kind") == "transport" or query.get("kind") == "place"
                 and re.search(r"在哪里|在哪儿|地址|怎么去", message))):
            return "lookup", message, query
        return "lookup", message, {**(query if query.get("kind") == kind else {}), "kind": kind}
    if (guess == "lookup" and query.get("kind") in ("weather", "rail", "place", "transport")
            and not (GENERAL.search(message) or re.search(r"什么是|是什么|有什么区别|什么意思", message))):
        return "lookup", message, query
    return "chat", message, {}
