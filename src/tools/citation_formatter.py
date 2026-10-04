"""Citation formatter — APA, MLA, GB/T 7714 styles."""


class CitationFormatter:
    """Formats citations in various academic styles."""

    STYLES = ["apa", "mla", "gbt7714", "numeric"]

    @staticmethod
    def format_apa(paper: dict) -> str:
        """APA 7th edition format: Author, A. A. (Year). Title. Source."""
        authors = paper.get("authors", [])
        if authors:
            if len(authors) == 1:
                author_str = authors[0]
            elif len(authors) == 2:
                author_str = f"{authors[0]} & {authors[1]}"
            else:
                author_str = f"{authors[0]} et al."
        else:
            author_str = "Unknown"

        year = paper.get("year", "n.d.")
        title = paper.get("title", "Untitled")
        source = paper.get("source", paper.get("url", ""))

        if source:
            return f"{author_str} ({year}). {title}. {source}."
        return f"{author_str} ({year}). {title}."

    @staticmethod
    def format_mla(paper: dict) -> str:
        """MLA 9th edition format: Author. "Title." Source, Year."""
        authors = paper.get("authors", [])
        if authors:
            if len(authors) == 1:
                author_str = authors[0]
            elif len(authors) == 2:
                parts = authors[0].split()
                author_str = f"{authors[1]}, {parts[-1]}" if parts else authors[0]
            else:
                author_str = f"{authors[0]} et al."
        else:
            author_str = "Unknown"

        title = paper.get("title", "Untitled")
        source = paper.get("source", "")
        year = paper.get("year", "n.d.")

        return f'{author_str}. "{title}." {source}, {year}.'

    @staticmethod
    def format_gbt7714(paper: dict) -> str:
        """GB/T 7714-2015 format: 作者. 题名[J]. 刊名, 年, 卷(期): 起止页码."""
        authors = paper.get("authors", [])
        if authors:
            if len(authors) <= 3:
                author_str = ", ".join(authors)
            else:
                author_str = f"{', '.join(authors[:3])}, 等"
        else:
            author_str = "佚名"

        title = paper.get("title", "未标题")
        journal = paper.get("journal", paper.get("source", ""))
        year = paper.get("year", 0)
        doi = paper.get("doi", "")

        ref = f"{author_str}. {title}[J]."
        if journal:
            ref += f" {journal},"
        if year:
            ref += f" {year}"
        if doi:
            ref += f". DOI: {doi}"
        ref += "."
        return ref

    @staticmethod
    def format_numeric(paper: dict, index: int) -> str:
        """Numeric format: [index] Authors. Title. Source, Year."""
        authors = paper.get("authors", [])
        if authors:
            if len(authors) <= 3:
                author_str = ", ".join(authors)
            else:
                author_str = f"{authors[0]} et al."
        else:
            author_str = "Unknown"

        title = paper.get("title", "Untitled")
        year = paper.get("year", "")
        source = paper.get("source", "")

        parts = [f"[{index}]", author_str, f'"{title}."']
        if source:
            parts.append(source)
        if year:
            parts.append(str(year))
        return " ".join(parts) + "."

    @classmethod
    def format(cls, paper: dict, style: str = "gbt7714", index: int = 1) -> str:
        """Format a single paper citation in the given style."""
        if style == "apa":
            return cls.format_apa(paper)
        elif style == "mla":
            return cls.format_mla(paper)
        elif style == "numeric":
            return cls.format_numeric(paper, index)
        else:  # gbt7714 default
            return cls.format_gbt7714(paper)

    @classmethod
    def format_bibliography(cls, papers: list[dict], style: str = "gbt7714") -> str:
        """Format an entire bibliography."""
        lines = []
        for i, paper in enumerate(papers, 1):
            lines.append(cls.format(paper, style, i))
        return "\n\n".join(lines)
