import os
import random
import datetime
from fpdf import FPDF

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GRIDS_DIR = os.path.join(SCRIPT_DIR, "grids")


class NameGrid:
    def __init__(self):
        self.grid = [["" for _ in range(11)] for _ in range(11)]
        self.numbers_generated = False

    def generate_numbers(self):
        for col in range(1, 11):
            self.grid[0][col] = str(random.randint(0, 9))
        for row in range(1, 11):
            self.grid[row][0] = str(random.randint(0, 9))
        self.numbers_generated = True

    def add_name(self, row, col, name):
        if not (1 <= row <= 10 and 1 <= col <= 10):
            return False
        self.grid[row][col] = name
        return True

    def clear_cell(self, row, col):
        if not (1 <= row <= 10 and 1 <= col <= 10):
            return False
        self.grid[row][col] = ""
        return True

    def get_cell(self, row, col):
        return self.grid[row][col]

    def is_complete(self):
        return any(
            self.grid[r][c] != ""
            for r in range(1, 11)
            for c in range(1, 11)
        )

    def __str__(self):
        widths = []
        for col in range(11):
            w = max(len(self.grid[row][col]) for row in range(11))
            widths.append(max(w, 4))

        lines = []
        for row_idx, row in enumerate(self.grid):
            cells = [row[c].center(widths[c]) for c in range(11)]
            lines.append(" | ".join(cells))
            if row_idx == 0:
                lines.append("-+-".join("-" * widths[c] for c in range(11)))

        return "\n".join(lines)


def export_grid_to_pdf(grid, output_dir=None):
    if output_dir is None:
        output_dir = GRIDS_DIR
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"grid_{timestamp}.pdf"
    filepath = os.path.join(output_dir, filename)

    pdf = FPDF(orientation="L", format="A4")
    pdf.add_page()
    pdf.set_auto_page_break(auto=False)

    # Title
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 14, "Number Football Grid", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(6)

    # Grid dimensions
    table_width = pdf.w - pdf.l_margin - pdf.r_margin
    cell_w = table_width / 11
    cell_h = 14
    start_x = pdf.l_margin
    start_y = pdf.get_y()

    for row_idx in range(11):
        for col_idx in range(11):
            x = start_x + col_idx * cell_w
            y = start_y + row_idx * cell_h
            text = grid.get_cell(row_idx, col_idx)

            # Header row/column styling
            if row_idx == 0 or col_idx == 0:
                pdf.set_fill_color(210, 210, 210)
                pdf.set_font("Helvetica", "B", 11)
                fill = True
            else:
                pdf.set_fill_color(255, 255, 255)
                pdf.set_font("Helvetica", "", 9)
                fill = True  # white fill for clean look

            # Top-left corner cell
            if row_idx == 0 and col_idx == 0:
                pdf.set_fill_color(180, 180, 180)

            pdf.set_xy(x, y)
            pdf.cell(cell_w, cell_h, text[:12], border=1, align="C", fill=fill)

    # Footer
    pdf.set_y(-15)
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(
        0, 10,
        f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        align="C",
    )

    pdf.output(filepath)
    return os.path.abspath(filepath)
