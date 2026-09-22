"""Click CLI commands for one-time data seeding and management."""

import ast
import logging
from pathlib import Path

import click

logger = logging.getLogger(__name__)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def cli(verbose: bool) -> None:
    """BeerTracker management CLI."""
    from beertracker.logging_setup import configure_logging

    configure_logging()
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


@cli.command()
@click.argument("file_path", type=click.Path(exists=True, readable=True))
@click.option("--dry-run", is_flag=True, help="Parse but do not write to Firestore")
def seed_cards(file_path: str, dry_run: bool) -> None:
    """Seed cards collection from a Java Map.toString() dump file.

    FILE_PATH should point to a text file containing a Python/Java dict literal
    mapping 10-digit card numbers to lists of amounts (in kroner).
    """
    from beertracker.firebase.client import get_firestore_client
    from beertracker.firebase.repositories import CardRepository

    path = Path(file_path)
    content = path.read_text(encoding="utf-8").strip()

    # Java Map.toString uses {key=[...], ...} — convert to Python dict
    try:
        # Try ast.literal_eval directly first (file might already be valid Python)
        data = ast.literal_eval(content)
    except (ValueError, SyntaxError) as exc:
        click.echo(f"Direct parse failed ({exc}), trying fallback parser...")
        data = _parse_java_map(content)

    if not isinstance(data, dict):
        raise click.BadParameter(f"File does not contain a valid map ({type(data).__name__})")

    # Aggregate sums per card
    card_totals: dict[str, float] = {}
    for card_id, amounts in data.items():
        card_str = str(card_id).strip()
        try:
            total = sum(float(a) for a in amounts) if isinstance(amounts, list) else float(amounts)
        except (TypeError, ValueError):
            total = 0.0
        card_totals[card_str] = total

    click.echo(f"Parsed {len(card_totals)} cards from {file_path}")

    if dry_run:
        click.echo("DRY RUN — would write the following:")
        for idx, (cid, total) in enumerate(sorted(card_totals.items()), 1):
            click.echo(f"  {idx}. {cid} -> {total:.2f} kr")
        return

    db = get_firestore_client()
    card_repo = CardRepository(db)

    written = 0
    with click.progressbar(sorted(card_totals.items()), label="Seeding cards") as items:
        for card_id, total in items:
            card_repo.upsert_sum(card_id, total)
            written += 1

    click.echo(f"Successfully seeded {written} cards into Firestore.")


@cli.command()
@click.argument("hash_file", type=click.Path(exists=True, readable=True))
def seed_state(hash_file: str) -> None:
    """Write the lastPurchaseHash from a file into Firestore info/state.

    HASH_FILE should contain a single line with the hash string.
    """
    from beertracker.firebase.client import get_firestore_client
    from beertracker.firebase.repositories import StateRepository

    path = Path(hash_file)
    hash_value = path.read_text(encoding="utf-8").strip()
    # Remove any comment lines after the hash
    hash_value = hash_value.splitlines()[0].strip()

    click.echo(f"Setting lastPurchaseHash to: {hash_value}")

    db = get_firestore_client()
    state_repo = StateRepository(db)
    state_repo.set_last_purchase_hash(hash_value)
    click.echo("Done.")


@cli.command()
@click.option("--collection", default="cards", help="Firestore collection to inspect")
def inspect(collection: str) -> None:
    """Quick Firestore inspection of a collection."""
    from beertracker.firebase.client import get_firestore_client

    db = get_firestore_client()
    docs = list(db.collection(collection).limit(5).stream())
    click.echo(f"Collection '{collection}' — {len(docs)} sample documents:")
    for doc in docs:
        click.echo(f"  {doc.id}: {doc.to_dict()}")


def _parse_java_map(content: str) -> dict[str, list[float]]:
    """Fallback parser for Java Map.toString() format.

    Example input:
        {4925562079=[37.0], 4106512455=[12.5, 8.0]}
    """
    content = content.strip()
    if content.startswith("{") and content.endswith("}"):
        content = content[1:-1]

    result: dict[str, list[float]] = {}
    pairs = []
    depth = 0
    current = ""
    for char in content:
        if char == "[":
            depth += 1
            current += char
        elif char == "]":
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            pairs.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        pairs.append(current.strip())

    for pair in pairs:
        if "=" not in pair:
            continue
        key_part, val_part = pair.split("=", 1)
        key = key_part.strip()
        val_part = val_part.strip()
        if val_part.startswith("[") and val_part.endswith("]"):
            val_str = val_part[1:-1]
            if not val_str:
                amounts = []
            else:
                amounts = [float(x.strip()) for x in val_str.split(",")]
        else:
            amounts = [float(val_part)]
        result[key] = amounts

    return result


def main() -> None:
    """Entry point for the CLI."""
    cli()

