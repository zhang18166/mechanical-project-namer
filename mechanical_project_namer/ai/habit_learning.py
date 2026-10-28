# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""Bounded local detection of response/workflow preferences, never engineering values."""
import re


def detect_habits(text: str) -> list[dict]:
    if len(text) > 2000 or re.search(r"[\"“”‘’>]|例如|比如|假如|如果|他说|客户说|AI说|不要记|别记|不保存", text):
        return []
    found = {}
    for sentence in re.split(r"[。！？\n]", text):
        sentence = sentence.strip()
        if not sentence or re.search(r"本次|这次|此次|暂时|今天|本项目|这个项目|当前项目|这台|这个零件|仅这|只这", sentence):
            continue
        strong = bool(re.match(r"^(?:请|麻烦)?(?:你)?(?:以后|今后|往后|每次|一直|默认)", sentence) or re.match(r"^我(?:习惯|喜欢|更喜欢|希望|偏好)", sentence))
        if re.search(r"不(?:要|用|必|需).*?(?:简洁|简短|精简)|不要.*?省略", sentence):
            length = '回答详细说明依据和步骤。'
        elif re.search(r"简洁|简短|精简|太啰嗦|少啰嗦|短一点|短些|短点|不(?:要|用|必|需).*详细", sentence):
            length = '回答保持简洁。'
        elif re.search(r"详细(?:一点|点|些|说明|回答)?|展开讲|多解释", sentence):
            length = '回答详细说明依据和步骤。'
        else:
            length = None
        # Avoid treating a request for a short part name or structural dimensions as style.
        if length and (re.search(r"回答|回复|说|解释|讲|啰嗦", sentence) or re.fullmatch(r"(?:请)?(?:简洁|简短|精简|详细|短)(?:一点|点|些)?[，, ]*", sentence)):
            found['answer_length'] = dict(key='answer_length', content=length, strong=strong)
        if re.search(r"(?:不要|别|不必).*先.*结论", sentence):
            pass
        elif re.search(r"先(?:给|说|讲|列|写|看)?(?:出)?结论", sentence):
            found['answer_order'] = dict(key='answer_order', content='回答先给结论，再说明依据和步骤。', strong=strong)
        elif re.search(r"先(?:讲|说|给)?分析.*(?:最后|再)(?:给|说)?结论", sentence):
            found['answer_order'] = dict(key='answer_order', content='回答先说明分析过程，最后给结论。', strong=strong)
        if re.search(r"计算.*(?:列|给|写|带|附).*公式", sentence) and not re.search(r"(?:不要|别|不必).*公式", sentence):
            found['calculation'] = dict(key='calculation', content='计算时列出公式和依据。', strong=strong)
        if re.search(r"(?:一次|每次).*只(?:问|提问)(?:一个|1个)|一次.*一个问题", sentence) and not re.search(r"(?:不要|别|不必).*一个", sentence):
            found['question_count'] = dict(key='question_count', content='需要追问时，每次只问一个关键问题。', strong=strong)
        if re.search(r"(?:回答|回复|说明|解释).*中文|用中文(?:回答|回复|解释)?", sentence) and not re.search(r"(?:不要|别|不必|不用).*中文", sentence):
            found['language'] = dict(key='language', content='使用中文回答。', strong=strong)
    return list(found.values())[:5]


def habit_keys(text: str) -> dict[str, str]:
    return {h['key']: h['content'] for h in detect_habits(text)}
