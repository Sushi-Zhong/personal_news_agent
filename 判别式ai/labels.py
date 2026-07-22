"""数据集中的 11 类标签。"""

TAG_NAMES = (
    "不违规",
    "偏见歧视",
    "淫秽色情",
    "财产隐私",
    "心理健康",
    "违法犯罪",
    "脏话侮辱",
    "身体伤害",
    "政治错误",
    "道德伦理",
    "变体词",
)
NAME_TO_TAG = {name: index + 1 for index, name in enumerate(TAG_NAMES)}
TAG_TO_NAME = {index + 1: name for index, name in enumerate(TAG_NAMES)}
