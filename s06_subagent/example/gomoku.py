#!/usr/bin/env python3
"""Simple command-line Gomoku (五子棋) for human-vs-human or human-vs-AI play."""

BOARD_SIZE = 15
EMPTY = "."
BLACK = "X"
WHITE = "O"
COLUMNS = "ABCDEFGHIJKLMNO"
DIRECTIONS = [(1, 0), (0, 1), (1, 1), (1, -1)]


def create_board():
    """Return an empty 15x15 board."""
    return [[EMPTY for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]


def display_board(board):
    """Print the board with row/column labels."""
    header = "   " + " ".join(COLUMNS)
    print(header)
    for index, row in enumerate(board, start=1):
        print(f"{index:>2} " + " ".join(row))


def parse_move(text):
    """Parse input like H8 or 8 H into zero-based row/col coordinates."""
    cleaned = text.strip().upper().replace(",", " ")
    if cleaned in {"Q", "QUIT", "EXIT"}:
        return "quit"

    parts = cleaned.split()
    if len(parts) == 1:
        token = parts[0]
        letters = "".join(ch for ch in token if ch.isalpha())
        digits = "".join(ch for ch in token if ch.isdigit())
    elif len(parts) == 2:
        if parts[0].isalpha() and parts[1].isdigit():
            letters, digits = parts[0], parts[1]
        elif parts[1].isalpha() and parts[0].isdigit():
            letters, digits = parts[1], parts[0]
        else:
            raise ValueError("Enter coordinates like H8 or 8 H.")
    else:
        raise ValueError("Enter coordinates like H8 or 8 H.")

    if len(letters) != 1 or letters not in COLUMNS:
        raise ValueError(f"Column must be A-{COLUMNS[-1]}.")
    if not digits:
        raise ValueError(f"Row must be 1-{BOARD_SIZE}.")

    row = int(digits) - 1
    col = COLUMNS.index(letters)
    if not (0 <= row < BOARD_SIZE):
        raise ValueError(f"Row must be 1-{BOARD_SIZE}.")
    return row, col


def format_move(row, col):
    """Return a user-friendly move like H8."""
    return f"{COLUMNS[col]}{row + 1}"


def is_valid_move(board, row, col):
    """Return True if the cell is within bounds and empty."""
    return 0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE and board[row][col] == EMPTY


def check_win(board, row, col, stone):
    """Return True if placing stone at row/col makes five in a row."""
    for dr, dc in DIRECTIONS:
        count = 1
        for step in (1, -1):
            r, c = row + dr * step, col + dc * step
            while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == stone:
                count += 1
                r += dr * step
                c += dc * step
        if count >= 5:
            return True
    return False


def board_full(board):
    """Return True when no empty cells remain."""
    return all(cell != EMPTY for row in board for cell in row)


def has_any_stones(board):
    """Return True if at least one move has been played."""
    return any(cell != EMPTY for row in board for cell in row)


def choose_game_mode():
    """Prompt for human-vs-human or human-vs-AI mode."""
    while True:
        choice = input("Choose mode: 1) Human vs Human  2) Human vs AI: ").strip().lower()
        if choice in {"1", "h", "hh", "human", "human vs human", "human-human", "pvp"}:
            return "hvh"
        if choice in {"2", "a", "ai", "human vs ai", "human-ai", "pve"}:
            return "ai"
        print("Please enter 1 or 2.\n")


def find_immediate_winning_move(board, stone):
    """Return a move that wins immediately for stone, or None."""
    for row in range(BOARD_SIZE):
        for col in range(BOARD_SIZE):
            if not is_valid_move(board, row, col):
                continue
            board[row][col] = stone
            wins = check_win(board, row, col, stone)
            board[row][col] = EMPTY
            if wins:
                return row, col
    return None


def has_neighboring_stone(board, row, col, radius=2):
    """Return True if a stone exists within the given radius."""
    for dr in range(-radius, radius + 1):
        for dc in range(-radius, radius + 1):
            if dr == 0 and dc == 0:
                continue
            r = row + dr
            c = col + dc
            if 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] != EMPTY:
                return True
    return False


def count_direction(board, row, col, dr, dc, stone):
    """Count consecutive stones from the adjacent cell in one direction."""
    count = 0
    r = row + dr
    c = col + dc
    while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == stone:
        count += 1
        r += dr
        c += dc
    return count


def score_move(board, row, col, stone):
    """Score a non-winning move based on local patterns and center bias."""
    center = (BOARD_SIZE - 1) / 2
    distance = abs(row - center) + abs(col - center)
    score = (BOARD_SIZE * 2) - distance

    if has_neighboring_stone(board, row, col, radius=1):
        score += 20
    elif has_neighboring_stone(board, row, col, radius=2):
        score += 8

    opponent = BLACK if stone == WHITE else WHITE

    for current_stone, weight in ((stone, 12), (opponent, 10)):
        for dr, dc in DIRECTIONS:
            forward = count_direction(board, row, col, dr, dc, current_stone)
            backward = count_direction(board, row, col, -dr, -dc, current_stone)
            total = forward + backward
            score += total * total * weight

    return score


def choose_ai_move(board, ai_stone, human_stone):
    """Pick an AI move: win, block, then prefer nearby moves with center bias."""
    winning_move = find_immediate_winning_move(board, ai_stone)
    if winning_move is not None:
        return winning_move

    blocking_move = find_immediate_winning_move(board, human_stone)
    if blocking_move is not None:
        return blocking_move

    if not has_any_stones(board):
        center = BOARD_SIZE // 2
        return center, center

    best_move = None
    best_score = None

    for row in range(BOARD_SIZE):
        for col in range(BOARD_SIZE):
            if not is_valid_move(board, row, col):
                continue
            score = score_move(board, row, col, ai_stone)
            move = (row, col)
            if best_score is None or score > best_score or (score == best_score and move < best_move):
                best_score = score
                best_move = move

    return best_move


def main():
    """Run the Gomoku game loop."""
    board = create_board()
    mode = choose_game_mode()
    players = [("Black", BLACK), ("White", WHITE)]
    turn = 0

    print("\nGomoku (五子棋) - Command Line Game")
    print("Enter moves like H8 or 8 H. Type q to quit.")
    if mode == "ai":
        print("AI mode: You are Black (X). The AI is White (O).\n")
    else:
        print()

    while True:
        display_board(board)
        name, stone = players[turn]
        ai_turn = mode == "ai" and stone == WHITE

        if ai_turn:
            row, col = choose_ai_move(board, WHITE, BLACK)
            print(f"{name} ({stone}) move: {format_move(row, col)}")
        else:
            move_text = input(f"{name} ({stone}) move: ")

            try:
                move = parse_move(move_text)
                if move == "quit":
                    print("Game ended by player.")
                    return
                row, col = move
            except ValueError as exc:
                print(f"Invalid input: {exc}\n")
                continue

            if not is_valid_move(board, row, col):
                print("Invalid move: that position is occupied or out of bounds.\n")
                continue

        board[row][col] = stone

        if check_win(board, row, col, stone):
            display_board(board)
            print(f"{name} ({stone}) wins!")
            return

        if board_full(board):
            display_board(board)
            print("The board is full. It's a draw!")
            return

        turn = 1 - turn
        print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nGame interrupted.")
