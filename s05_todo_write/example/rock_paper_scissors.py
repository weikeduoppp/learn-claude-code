"""简单的剪刀石头布命令行小游戏。"""

import random

CHOICES: tuple[str, str, str] = ("石头", "剪刀", "布")
WIN_RULES: dict[str, str] = {
    "石头": "剪刀",
    "剪刀": "布",
    "布": "石头",
}


def get_computer_choice() -> str:
    """随机返回电脑的出拳。"""
    return random.choice(CHOICES)


def get_result(player_choice: str, computer_choice: str) -> str:
    """根据双方出拳返回胜负结果。"""
    if player_choice == computer_choice:
        return "平局"
    if WIN_RULES[player_choice] == computer_choice:
        return "你赢了"
    return "你输了"


def main() -> None:
    """运行剪刀石头布小游戏。"""
    print("欢迎来到剪刀石头布小游戏！")
    print("请输入：石头 / 剪刀 / 布，输入 q 退出。")

    while True:
        player_choice = input("你的选择：").strip()

        if player_choice.lower() == "q":
            print("游戏结束，再见！")
            break

        if player_choice not in CHOICES:
            print("输入无效，请输入：石头 / 剪刀 / 布")
            continue

        computer_choice = get_computer_choice()
        result = get_result(player_choice, computer_choice)

        print(f"你出了：{player_choice}")
        print(f"电脑出了：{computer_choice}")
        print(result)
        print("-" * 20)


if __name__ == "__main__":
    main()
