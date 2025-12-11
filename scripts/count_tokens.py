# /// script
# dependencies = [
#   "tiktoken",
# ]
# requires-python = ">=3.10"
# ///
#!/usr/bin/env python3
"""
Token counter using tiktoken library.

This script counts tokens in text using the tiktoken library.
It supports:
- Direct text input via --text
- Single text file via --file
- Directory of text files via --dir (recursively processes text/code files)

Default encoding model: gpt-5 (o200k_base)

By default, processes common text and programming file extensions including:
.txt, .md, .py, .js, .ts, .jsx, .tsx, .rs, .go, .java, .c, .cpp, .h, .hpp,
.cs, .php, .rb, .swift, .kt, .scala, .sh, .bash, .zsh, .fish, .ps1, .bat,
.cmd, .html, .css, .scss, .sass, .less, .json, .yaml, .yml, .toml, .xml,
.sql, .r, .m, .mm, .vue, .svelte, .astro, .zig, .dart, .lua, .pl, .pm,
.ex, .exs, .clj, .elm, .erl, .hrl, .fs, .fsx, .jl, .nim, .ml, .mli
"""

import argparse
from pathlib import Path
import sys

import tiktoken


# Common text and programming file extensions
DEFAULT_EXTENSIONS = [
    # Documentation and text
    "*.txt", "*.md", "*.markdown", "*.rst", "*.adoc", "*.asciidoc",
    # Python
    "*.py", "*.pyw", "*.pyx", "*.pyi",
    # JavaScript/TypeScript
    "*.js", "*.mjs", "*.cjs", "*.ts", "*.mts", "*.cts", "*.jsx", "*.tsx",
    # Rust
    "*.rs",
    # Go
    "*.go",
    # Java/Kotlin/Scala
    "*.java", "*.kt", "*.kts", "*.scala",
    # C/C++
    "*.c", "*.cpp", "*.cc", "*.cxx", "*.h", "*.hpp", "*.hh", "*.hxx",
    # C#
    "*.cs", "*.csx",
    # PHP
    "*.php", "*.phtml",
    # Ruby
    "*.rb", "*.rake", "*.gemspec",
    # Swift
    "*.swift",
    # Objective-C
    "*.m", "*.mm",
    # Shell scripts
    "*.sh", "*.bash", "*.zsh", "*.fish", "*.ps1", "*.bat", "*.cmd",
    # Web (HTML/CSS)
    "*.html", "*.htm", "*.xhtml", "*.css", "*.scss", "*.sass", "*.less",
    # Web frameworks
    "*.vue", "*.svelte", "*.astro",
    # Data formats
    "*.json", "*.jsonc", "*.json5", "*.yaml", "*.yml", "*.toml", "*.ini", "*.cfg", "*.conf", "*.xml",
    # SQL
    "*.sql",
    # R
    "*.r", "*.R",
    # Zig
    "*.zig",
    # Dart
    "*.dart",
    # Lua
    "*.lua",
    # Perl
    "*.pl", "*.pm", "*.t",
    # Elixir
    "*.ex", "*.exs",
    # Clojure
    "*.clj", "*.cljs", "*.cljc", "*.edn",
    # Elm
    "*.elm",
    # Erlang
    "*.erl", "*.hrl",
    # F#
    "*.fs", "*.fsx", "*.fsi",
    # Julia
    "*.jl",
    # Nim
    "*.nim", "*.nims",
    # OCaml
    "*.ml", "*.mli",
    # Haskell
    "*.hs", "*.lhs",
    # Makefile and config
    "Makefile", "*.mk", "Dockerfile", "*.dockerfile",
    # Other
    "*.proto", "*.graphql", "*.gql", "*.tex", "*.bib",
]


def count_tokens(text: str, model: str = "gpt-5") -> int:
    """
    Count the number of tokens in the given text using the specified model.
    
    Args:
        text: The text to tokenize
        model: The model name (default: gpt-5)
    
    Returns:
        Number of tokens
    """
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        print(f"Warning: Model '{model}' not found, falling back to o200k_base encoding", file=sys.stderr)
        encoding = tiktoken.get_encoding("o200k_base")
    
    tokens = encoding.encode(text)
    return len(tokens)


def count_tokens_in_file(file_path: Path, model: str = "gpt-5") -> int:
    """
    Count tokens in a single file.
    
    Args:
        file_path: Path to the text file
        model: The model name
    
    Returns:
        Number of tokens
    """
    try:
        text = file_path.read_text(encoding="utf-8")
        return count_tokens(text, model)
    except UnicodeDecodeError:
        try:
            text = file_path.read_text(encoding="latin-1")
            return count_tokens(text, model)
        except Exception as e:
            print(f"Error reading {file_path}: {e}", file=sys.stderr)
            return 0
    except Exception as e:
        print(f"Error reading {file_path}: {e}", file=sys.stderr)
        return 0


def count_tokens_in_directory(
    dir_path: Path, 
    model: str = "gpt-5", 
    patterns: list[str] | None = None
) -> dict[str, int]:
    """
    Count tokens in all matching files in a directory (recursively).
    
    Args:
        dir_path: Path to the directory
        model: The model name
        patterns: List of glob patterns for files to process (default: DEFAULT_EXTENSIONS)
    
    Returns:
        Dictionary mapping file paths to token counts
    """
    if patterns is None:
        patterns = DEFAULT_EXTENSIONS
    
    results = {}
    processed_files = set()
    
    for pattern in patterns:
        for file_path in dir_path.rglob(pattern):
            if file_path.is_file() and str(file_path) not in processed_files:
                token_count = count_tokens_in_file(file_path, model)
                results[str(file_path)] = token_count
                processed_files.add(str(file_path))
    
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Count tokens in text using tiktoken",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    # Input sources (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--text",
        type=str,
        help="Direct text input to count tokens"
    )
    input_group.add_argument(
        "--file",
        type=Path,
        help="Path to a single text file"
    )
    input_group.add_argument(
        "--dir",
        type=Path,
        help="Path to a directory containing text files"
    )
    
    # Optional parameters
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-5",
        help="Model name for tokenization (default: gpt-5)"
    )
    parser.add_argument(
        "--pattern",
        type=str,
        action="append",
        dest="patterns",
        help="File pattern(s) for directory processing (can be specified multiple times). "
             "If not specified, uses default patterns for common text/code files."
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed output"
    )
    parser.add_argument(
        "--show-patterns",
        action="store_true",
        help="Show default file patterns and exit"
    )
    
    args = parser.parse_args()
    
    # Show default patterns if requested
    if args.show_patterns:
        print("Default file patterns:")
        for pattern in DEFAULT_EXTENSIONS:
            print(f"  {pattern}")
        sys.exit(0)
    
    # Process based on input type
    if args.text:
        # Direct text input
        token_count = count_tokens(args.text, args.model)
        if args.verbose:
            print(f"Text: {args.text[:50]}{'...' if len(args.text) > 50 else ''}")
            print(f"Model: {args.model}")
            print(f"Token count: {token_count}")
        else:
            print(token_count)
    
    elif args.file:
        # Single file
        if not args.file.exists():
            print(f"Error: File not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        
        token_count = count_tokens_in_file(args.file, args.model)
        if args.verbose:
            print(f"File: {args.file}")
            print(f"Model: {args.model}")
            print(f"Token count: {token_count}")
        else:
            print(token_count)
    
    elif args.dir:
        # Directory
        if not args.dir.exists() or not args.dir.is_dir():
            print(f"Error: Directory not found: {args.dir}", file=sys.stderr)
            sys.exit(1)
        
        results = count_tokens_in_directory(args.dir, args.model, args.patterns)
        
        if not results:
            pattern_info = f"pattern(s): {', '.join(args.patterns)}" if args.patterns else "default patterns"
            print(f"No files matching {pattern_info} found in {args.dir}", file=sys.stderr)
            sys.exit(1)
        
        total_tokens = 0
        for file_path, token_count in results.items():
            if args.verbose:
                print(f"{file_path}: {token_count:,} tokens")
            total_tokens += token_count
        
        if args.verbose:
            print(f"\nTotal files: {len(results)}")
            print(f"Total tokens: {total_tokens:,}")
        else:
            print(total_tokens)


if __name__ == "__main__":
    main()
