# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from .models import RawDeckEntry

RAW_DECKS: tuple[RawDeckEntry, ...] = (
    RawDeckEntry("natlan_battleship", "纳塔战舰", "yz你好zy", ("整活",), "HeDh8d0PHRBh9OEPHkERotgaHSGCxCYcIkFhy+QcHrFA270dC7HRr6AaGvEA1MMdDUAA"),
    RawDeckEntry("klee_mavuika_mualani", "可莉玛玛拉妮", "歆与笙", (), "ATBh5N4OG0HB6eEPHkAS9CYQIjFhA5MTGbExO50UGcHR27kdG7GQ+dsfI5FwDOMQDsAA"),
    RawDeckEntry("superconduct_mika_attack", "【超导米卡】普攻流伤害爆炸", "桐岛同学想要坏心眼", ("超导otk", "元素反应", "简单"), "IjGxVx4VIXFSky4ZIjDg2owjCGDA344NCPDh4C4OEgDg9pcPCWFxOzETE7ERDDMQE8AA"),
    RawDeckEntry("dual_mualani_stacks", "双玛叠层流v2.0", "很肝的爷", ("切人", "叠层", "巨额伤害"), "GzDB9F4PHUDh9b4PFmERO5MTE7FRorkaGyGRxKEcDUGy2zcdDrEQC+EaDvEg1OIdD0AA"),
    RawDeckEntry("superconduct_aggro", "超导——", "尹ouo", ("超导", "快攻", "大招流"), "AdLANiAOAFAQ5ZcOCWBw5pkOCeCg7poPIUAQ9L0PE1BQ9cMPDGBA+cQQDaGR1DodIUAA"),
    RawDeckEntry("mualani_double_pyro", "玛玛双火", "孤孤孤孤孤孤孤147253", ("元素反应", "整活", "蒸发"), "AUGB6d4PG2DB9r4QHjERO5MTDLEAxMAcG7GRy7kQGpEQCdsQI8FwDOMTDuEwr+QaDvAA"),
    RawDeckEntry("yelan_clorinde_ororon", "夜克", "月之下影之上", ("整活", "元素反应"), "EZCB2b0NIrAQ5ZcOCVBx5jEOE2AR9FcPFUFxCJMQGYEyOwATDLGQDMkQDMGgEMoRDQAA"),
    RawDeckEntry("electrocharged_moon", "月月月月月感电", "木星的阿列克谢", ("月感电", "伊涅芙", "邪龙"), "IpDC2QsTAqAy5S8OIlDx5mMPFhAx9DMPE0Ax9rcPC2JAA7UgIzIiBjIgDGGg1ModDUAA"),
    RawDeckEntry("aywolfskirk", "爱狼丝(屠双龙？宰丝恰？)", "影视I清澈", (), "IaFR2rIdIqACwiMMGyBR3rUNCPDg36QOCgBA4LYPCxFgA7oQCzGgisEYDKER1ModHEAA"),
    RawDeckEntry("dvalin_bonk", "特瓦林夯爆了", "龙璇渊鳞", ("扩散", "元素反应", "otk"), "EXDx5/oOFIAB6P0PFBBB8UQPGEAh9YIPDJEQlcEZHVGh6toeE6GB1DgdDUHh66MeDrAA"),
    RawDeckEntry("shenhe_double_swirl", "申鹤双扩", "龙璇渊鳞", ("扩散", "元素反应", "otk"), "EXDw6AcOFIAB8UQPFBBB9CkPGFAh9oIPDGAQ+cEPHcCh/DgeE6GA6t4dGkEw1OceDrAA"),
    RawDeckEntry("electro_manifestation_nuke", "雷音权现核爆流（超导祝佑版）", "夏尔芬夏尔", (), "HHEC3h8kAWFh4JgOCvFy4VgPFgFx8rUQDREx98cQDWJhDcoRDsIBFNISDjIS1WgeF0EB"),
    RawDeckEntry("double_geo_navia", "双岩娜维娅", "无裳自在门大弟子", ("双岩", "娜维娅", "结晶"), "HJEj6hYPGaHC65AZGZHy8rYQHBFR9bUQDEFB98cQDWJhadIXDoIS1WceF0Ji3egeD8EB"),
    RawDeckEntry("unyielding_geo", "千岩不移", "荷兰豚鼠", ("结晶",), "AmDCyRYaGxEg52gOG5BQ6YwOCaBw6p0OCeDR7tkPCxDQ9MEQDKERaTYWDJEg1MMhDEAA"),
    RawDeckEntry("beidou_skirk_ayaka", "6.5【北丝】2回合秒掉丝恰", "桐岛同学想要坏心眼", ("极致快攻", "元素超导", "otk"), "AaDi2SANAJBQ21ojBWCg35cNCfFxAzEQEzERB1cQFXFxCJMQGYEyOwATILEA6MoeDIAA"),
    RawDeckEntry("skirk_chasca_freeze", "丝恰水，无限冻结！", "AEvum", (), "I9ECweANFhLjZiQZI1IyhuMNHzIiS2IiCtFx9ZgQC0FB96UQG2Kyi6wgHZIS+sIhDEEB"),
    RawDeckEntry("skirk_ayaka_navia", "丝爱娜", "8169508243", ("天赋流",), "IvAC3xUOGADC4CMYGJDx9o8PCWFwipcYDKEQlsEeE4Fh6DYcDFFgxcYdDUHh1DodDcAA"),
    RawDeckEntry("ice_water_battleship", "冰水战舰宣传片", "Uling0", ("扩散", "冻结", "有丝必赞"), "HaHxZ2whItAC9CMPHkAh9uIPCmFBCFcQGoGxO6sTHLERisEYDKEQ6MEeGIFR+YUfFJAA"),
    RawDeckEntry("nahida_kokomi_raiden", "自留", "yeva瑶", (), "AyAQww0MAkAAyFwMBpDQynkMB7CwzIkNCGDQ2KcPChCw87MPC0BQ9rYPC3Bw/LgPDNAA"),
    RawDeckEntry("trial_skirk_ayaka_navia", "试用", "skddxjh", (), "IpICHRUNGPDC3yMOIgAx4I8PCGBg9oYTFbFwO8EYDKERioUeGIFQ6MYdDEFg3MkdDMAA"),
)

RAW_DECK_BY_SLUG = {entry.slug: entry for entry in RAW_DECKS}
