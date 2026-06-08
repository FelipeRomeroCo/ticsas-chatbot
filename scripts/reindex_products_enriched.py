import hashlib
import json
import os
from pathlib import Path
from typing import Any

import psycopg
import requests
from pgvector.psycopg import register_vector


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://ticsas:ticsas_password@db:5432/ticsas_chatbot",
)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
EMBEDDING_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")

CATALOG_FILE = os.getenv(import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import psycopg
import requests
from pgvector.psycopg import register_vector


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://ticsas:ticsas_password@db:5432/ticsas_chatbot",
)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
EMBEDDING_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")

CATALOG_FILE = os.getenv(
    "CATALOG_FILE",
    "/app/data/productos_ticsas_vectordb_documentos_enriquecido.jsonl",
)

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "25"))
DB_WAIT_SECONDS = int(os.getenv("DB_WAIT_SECONDS", "90"))
OLLAMA_WAIT_SECONDS = int(os.getenv("OLLAMA_WAIT_SECONDS", "120"))


def wait_for_database() -> None:
    deadline = time.time() + DB_WAIT_SECONDS
    last_error = None

    while time.time() < deadline:
        try:
            with psycopg.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1;")
                    cur.fetchone()

            print("Base de datos disponible.")
            return
        except Exception as error:
            last_error = error
            print("Esperando base de datos...")
            time.sleep(3)

    raise RuntimeError(
        f"No fue posible conectar con PostgreSQL después de {DB_WAIT_SECONDS} segundos. "
        f"Último error: {last_error}"
    )


def wait_for_ollama() -> None:
    deadline = time.time() + OLLAMA_WAIT_SECONDS
    last_error = None

    while time.time() < deadline:
        try:
            response = requests.get(
                f"{OLLAMA_BASE_URL}/api/version",
                timeout=10,
            )
            response.raise_for_status()

            print("Ollama disponible.")
            return
        except Exception as error:
            last_error = error
            print("Esperando Ollama...")
            time.sleep(3)

    raise RuntimeError(
        f"No fue posible conectar con Ollama después de {OLLAMA_WAIT_SECONDS} segundos. "
        f"Último error: {last_error}"
    )


def get_embedding(text: str) -> list[float]:
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/embed",
        json={
            "model": EMBEDDING_MODEL,
            "input": text,
        },
        timeout=120,
    )
    response.raise_for_status()

    data = response.json()

    embeddings = data.get("embeddings")

    if not isinstance(embeddings, list) or not embeddings:
        raise ValueError(
            "Ollama no devolvió embeddings. "
            "Verifica que el modelo de embeddings esté descargado."
        )

    embedding = embeddings[0]

    if not isinstance(embedding, list) or not embedding:
        raise ValueError("El embedding devuelto por Ollama está vacío o no es válido.")

    return [float(value) for value in embedding]


def to_pgvector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def get_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_jsonl(path: str) -> list[dict[str, Any]]:
    file_path = Path(path)

    if not file_path.exists():
        raise FileNotFoundError(f"No existe el archivo: {file_path}")

    rows = []

    with file_path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                document = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"JSON inválido en línea {line_number}: {error}"
                ) from error

            document["_line_number"] = line_number
            rows.append(document)

    if not rows:
        raise ValueError(f"El archivo JSONL no contiene documentos: {file_path}")

    return rows


def metadata_value(metadata: dict[str, Any], key: str, default: Any = None) -> Any:
    value = metadata.get(key, default)

    if value == "":
        return default

    return value


def metadata_number(metadata: dict[str, Any], key: str) -> float | None:
    value = metadata.get(key)

    if value is None or value == "":
        return None

    if isinstance(value, str):
        value = value.strip().replace(",", ".")

        if not value:
            return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_product_id(document: dict[str, Any]) -> str:
    metadata = document.get("metadata", {})

    product_id = (
        metadata.get("product_id")
        or metadata.get("source_id")
        or metadata.get("id")
        or document.get("product_id")
        or document.get("source_id")
        or document.get("id")
    )

    if not product_id:
        line_number = document.get("_line_number", "desconocida")
        raise ValueError(f"Producto sin identificador en línea {line_number}.")

    return str(product_id).strip()


def validate_documents(documents: list[dict[str, Any]]) -> None:
    seen_product_ids = set()

    for document in documents:
        product_id = resolve_product_id(document)
        embedding_text = str(document.get("text") or "").strip()

        if not embedding_text:
            line_number = document.get("_line_number", "desconocida")
            raise ValueError(
                f"Producto {product_id} sin texto para embedding en línea {line_number}."
            )

        if product_id in seen_product_ids:
            raise ValueError(f"Producto duplicado en JSONL: {product_id}")

        seen_product_ids.add(product_id)


def table_exists(conn: psycopg.Connection, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = %s
            );
            """,
            (table_name,),
        )

        result = cur.fetchone()

    return bool(result and result[0])


def get_table_columns(conn: psycopg.Connection, table_name: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s;
            """,
            (table_name,),
        )

        rows = cur.fetchall()

    return {row[0] for row in rows}


def ensure_schema(conn: psycopg.Connection, embedding_dimension: int) -> None:
    if embedding_dimension <= 0:
        raise ValueError("La dimensión del embedding debe ser mayor a cero.")

    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE EXTENSION IF NOT EXISTS vector;
            CREATE EXTENSION IF NOT EXISTS pg_trgm;
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id BIGSERIAL PRIMARY KEY,
                product_id TEXT NOT NULL UNIQUE,
                title TEXT,
                categoria_web TEXT,
                subcategoria_chatbot TEXT,
                precio NUMERIC,
                url TEXT,
                uso_detectado TEXT,
                especificaciones_detectadas TEXT,
                rag_text TEXT,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                product_type TEXT,
                product_family TEXT,
                search_aliases TEXT,
                canonical_terms TEXT,
                normalized_title TEXT,
                embedding_text TEXT,
                search_text TEXT,
                created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS product_embeddings (
                id BIGSERIAL PRIMARY KEY,
                product_id TEXT NOT NULL UNIQUE REFERENCES products(product_id) ON DELETE CASCADE,
                embedding VECTOR({embedding_dimension}) NOT NULL,
                embedding_model TEXT,
                model TEXT,
                content_hash TEXT,
                embedding_provider TEXT,
                provider TEXT,
                created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        cur.execute(
            """
            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS product_type TEXT;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS product_family TEXT;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS search_aliases TEXT;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS canonical_terms TEXT;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS normalized_title TEXT;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS embedding_text TEXT;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS search_text TEXT;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS rag_text TEXT;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;

            ALTER TABLE product_embeddings
            ADD COLUMN IF NOT EXISTS embedding_model TEXT;

            ALTER TABLE product_embeddings
            ADD COLUMN IF NOT EXISTS model TEXT;

            ALTER TABLE product_embeddings
            ADD COLUMN IF NOT EXISTS content_hash TEXT;

            ALTER TABLE product_embeddings
            ADD COLUMN IF NOT EXISTS embedding_provider TEXT;

            ALTER TABLE product_embeddings
            ADD COLUMN IF NOT EXISTS provider TEXT;

            ALTER TABLE product_embeddings
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;

            ALTER TABLE product_embeddings
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
            """
        )

        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_products_product_id_unique
            ON products(product_id);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_product_embeddings_product_id_unique
            ON product_embeddings(product_id);

            CREATE INDEX IF NOT EXISTS idx_products_active
            ON products(active);

            CREATE INDEX IF NOT EXISTS idx_products_product_type
            ON products(product_type);

            CREATE INDEX IF NOT EXISTS idx_products_product_family
            ON products(product_family);

            CREATE INDEX IF NOT EXISTS idx_products_categoria_web
            ON products(categoria_web);

            CREATE INDEX IF NOT EXISTS idx_products_subcategoria_chatbot
            ON products(subcategoria_chatbot);

            CREATE INDEX IF NOT EXISTS idx_products_title_trgm
            ON products USING gin(title gin_trgm_ops);

            CREATE INDEX IF NOT EXISTS idx_products_search_text_trgm
            ON products USING gin(search_text gin_trgm_ops);

            CREATE INDEX IF NOT EXISTS idx_products_canonical_terms_trgm
            ON products USING gin(canonical_terms gin_trgm_ops);

            CREATE INDEX IF NOT EXISTS idx_products_normalized_title_trgm
            ON products USING gin(normalized_title gin_trgm_ops);
            """
        )

    conn.commit()


def ensure_vector_index(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_product_embeddings_embedding_cosine
            ON product_embeddings
            USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100);

            ANALYZE products;
            ANALYZE product_embeddings;
            """
        )

    conn.commit()


def reset_catalog(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE product_embeddings RESTART IDENTITY;")
        cur.execute("TRUNCATE TABLE products RESTART IDENTITY CASCADE;")

    conn.commit()


def insert_product_row(
    conn: psycopg.Connection,
    document: dict[str, Any],
) -> None:
    metadata = document.get("metadata", {})
    product_id = resolve_product_id(document)
    embedding_text = str(document.get("text") or "")
    rag_text = metadata_value(metadata, "rag_text", "")
    search_text = metadata_value(metadata, "search_text", "")

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO products (
                product_id,
                title,
                categoria_web,
                subcategoria_chatbot,
                precio,
                url,
                uso_detectado,
                especificaciones_detectadas,
                rag_text,
                active,
                product_type,
                product_family,
                search_aliases,
                canonical_terms,
                normalized_title,
                embedding_text,
                search_text,
                updated_at
            )
            VALUES (
                %(product_id)s,
                %(title)s,
                %(categoria_web)s,
                %(subcategoria_chatbot)s,
                %(precio)s,
                %(url)s,
                %(uso_detectado)s,
                %(especificaciones_detectadas)s,
                %(rag_text)s,
                TRUE,
                %(product_type)s,
                %(product_family)s,
                %(search_aliases)s,
                %(canonical_terms)s,
                %(normalized_title)s,
                %(embedding_text)s,
                %(search_text)s,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT (product_id) DO UPDATE SET
                title = EXCLUDED.title,
                categoria_web = EXCLUDED.categoria_web,
                subcategoria_chatbot = EXCLUDED.subcategoria_chatbot,
                precio = EXCLUDED.precio,
                url = EXCLUDED.url,
                uso_detectado = EXCLUDED.uso_detectado,
                especificaciones_detectadas = EXCLUDED.especificaciones_detectadas,
                rag_text = EXCLUDED.rag_text,
                active = TRUE,
                product_type = EXCLUDED.product_type,
                product_family = EXCLUDED.product_family,
                search_aliases = EXCLUDED.search_aliases,
                canonical_terms = EXCLUDED.canonical_terms,
                normalized_title = EXCLUDED.normalized_title,
                embedding_text = EXCLUDED.embedding_text,
                search_text = EXCLUDED.search_text,
                updated_at = CURRENT_TIMESTAMP;
            """,
            {
                "product_id": product_id,
                "title": metadata_value(metadata, "title", ""),
                "categoria_web": metadata_value(metadata, "categoria_web", ""),
                "subcategoria_chatbot": metadata_value(
                    metadata,
                    "subcategoria_chatbot",
                    "",
                ),
                "precio": metadata_number(metadata, "precio"),
                "url": metadata_value(metadata, "url", ""),
                "uso_detectado": metadata_value(metadata, "uso_detectado", ""),
                "especificaciones_detectadas": metadata_value(
                    metadata,
                    "especificaciones_detectadas",
                    "",
                ),
                "rag_text": rag_text,
                "product_type": metadata_value(metadata, "product_type", ""),
                "product_family": metadata_value(metadata, "product_family", ""),
                "search_aliases": metadata_value(metadata, "search_aliases", ""),
                "canonical_terms": metadata_value(metadata, "canonical_terms", ""),
                "normalized_title": metadata_value(metadata, "normalized_title", ""),
                "embedding_text": embedding_text,
                "search_text": search_text,
            },
        )


def insert_embedding_row(
    conn: psycopg.Connection,
    product_id: str,
    embedding_text: str,
    embedding: list[float],
    product_embedding_columns: set[str],
) -> None:
    insert_columns = [
        "product_id",
        "embedding",
    ]

    values_sql = [
        "%(product_id)s",
        "%(embedding)s::vector",
    ]

    params = {
        "product_id": product_id,
        "embedding": to_pgvector_literal(embedding),
    }

    update_assignments = [
        "embedding = EXCLUDED.embedding",
    ]

    if "embedding_model" in product_embedding_columns:
        insert_columns.append("embedding_model")
        values_sql.append("%(embedding_model)s")
        params["embedding_model"] = EMBEDDING_MODEL
        update_assignments.append("embedding_model = EXCLUDED.embedding_model")

    if "model" in product_embedding_columns:
        insert_columns.append("model")
        values_sql.append("%(model)s")
        params["model"] = EMBEDDING_MODEL
        update_assignments.append("model = EXCLUDED.model")

    if "content_hash" in product_embedding_columns:
        insert_columns.append("content_hash")
        values_sql.append("%(content_hash)s")
        params["content_hash"] = get_content_hash(embedding_text)
        update_assignments.append("content_hash = EXCLUDED.content_hash")

    if "embedding_provider" in product_embedding_columns:
        insert_columns.append("embedding_provider")
        values_sql.append("%(embedding_provider)s")
        params["embedding_provider"] = "ollama"
        update_assignments.append("embedding_provider = EXCLUDED.embedding_provider")

    if "provider" in product_embedding_columns:
        insert_columns.append("provider")
        values_sql.append("%(provider)s")
        params["provider"] = "ollama"
        update_assignments.append("provider = EXCLUDED.provider")

    if "updated_at" in product_embedding_columns:
        insert_columns.append("updated_at")
        values_sql.append("CURRENT_TIMESTAMP")
        update_assignments.append("updated_at = CURRENT_TIMESTAMP")

    sql = f"""
        INSERT INTO product_embeddings (
            {", ".join(insert_columns)}
        )
        VALUES (
            {", ".join(values_sql)}
        )
        ON CONFLICT (product_id) DO UPDATE SET
            {", ".join(update_assignments)};
    """

    with conn.cursor() as cur:
        cur.execute(sql, params)


def insert_product(
    conn: psycopg.Connection,
    document: dict[str, Any],
    embedding: list[float],
    product_embedding_columns: set[str],
) -> None:
    product_id = resolve_product_id(document)
    embedding_text = str(document.get("text") or "")

    insert_product_row(conn, document)
    insert_embedding_row(
        conn,
        product_id,
        embedding_text,
        embedding,
        product_embedding_columns,
    )


def print_catalog_preview(documents: list[dict[str, Any]]) -> None:
    print("Primeros productos detectados:")

    for document in documents[:5]:
        metadata = document.get("metadata", {})
        product_id = resolve_product_id(document)
        title = metadata.get("title", "sin_titulo")
        product_type = metadata.get("product_type", "sin_tipo")
        product_family = metadata.get("product_family", "sin_familia")

        print(f"- {product_id} | {product_type} | {product_family} | {title}")


def print_embedding_table_info(product_embedding_columns: set[str]) -> None:
    columns = ", ".join(sorted(product_embedding_columns))
    print(f"Columnas detectadas en product_embeddings: {columns}")

    if "embedding_model" in product_embedding_columns:
        print(f"Se usará embedding_model = {EMBEDDING_MODEL}")

    if "model" in product_embedding_columns:
        print(f"Se usará model = {EMBEDDING_MODEL}")

    if "content_hash" in product_embedding_columns:
        print("Se calculará content_hash con sha256(text).")


def print_final_counts(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM products;")
        products_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM product_embeddings;")
        embeddings_count = cur.fetchone()[0]

    print(f"Productos cargados: {products_count}")
    print(f"Embeddings cargados: {embeddings_count}")


def main() -> None:
    print("Iniciando reindexación del catálogo TICSAS.")
    print(f"Archivo configurado: {CATALOG_FILE}")
    print(f"Base de datos: {DATABASE_URL}")
    print(f"Ollama: {OLLAMA_BASE_URL}")
    print(f"Modelo de embeddings: {EMBEDDING_MODEL}")

    wait_for_database()
    wait_for_ollama()

    documents = load_jsonl(CATALOG_FILE)
    validate_documents(documents)

    print(f"Documentos a indexar: {len(documents)}")
    print_catalog_preview(documents)

    print("Calculando embedding inicial para detectar dimensión del vector...")
    first_document = documents[0]
    first_embedding_text = str(first_document.get("text") or "")
    first_embedding = get_embedding(first_embedding_text)
    embedding_dimension = len(first_embedding)

    print(f"Dimensión detectada del embedding: {embedding_dimension}")

    with psycopg.connect(DATABASE_URL) as conn:
        ensure_schema(conn, embedding_dimension)
        register_vector(conn)

        product_embedding_columns = get_table_columns(conn, "product_embeddings")
        print_embedding_table_info(product_embedding_columns)

        reset_catalog(conn)

        first_product_id = resolve_product_id(first_document)
        first_title = first_document.get("metadata", {}).get("title", "sin_titulo")

        insert_product(
            conn,
            first_document,
            first_embedding,
            product_embedding_columns,
        )

        print(f"[1/{len(documents)}] {first_product_id} | {first_title}")

        for index, document in enumerate(documents[1:], start=2):
            metadata = document.get("metadata", {})
            product_id = resolve_product_id(document)
            title = metadata.get("title", "sin_titulo")
            embedding_text = str(document.get("text") or "")

            embedding = get_embedding(embedding_text)

            if len(embedding) != embedding_dimension:
                raise ValueError(
                    f"Dimensión inconsistente en producto {product_id}. "
                    f"Esperado: {embedding_dimension}. Recibido: {len(embedding)}."
                )

            insert_product(
                conn,
                document,
                embedding,
                product_embedding_columns,
            )

            if index % BATCH_SIZE == 0:
                conn.commit()
                print(f"Indexados {index}/{len(documents)} productos...")

            print(f"[{index}/{len(documents)}] {product_id} | {title}")

        conn.commit()

        print("Creando índices finales...")
        ensure_vector_index(conn)

        print_final_counts(conn)

    print("Reindexación finalizada correctamente.")


if __name__ == "__main__":
    main()
    "CATALOG_FILE",
    "/app/data/productos_ticsas_vectordb_documentos_enriquecido.jsonl",
)

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "25"))


def get_embedding(text: str) -> list[float]:
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/embed",
        json={
            "model": EMBEDDING_MODEL,
            "input": text,
        },
        timeout=120,
    )
    response.raise_for_status()

    data = response.json()
    return data["embeddings"][0]


def to_pgvector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def get_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_jsonl(path: str) -> list[dict[str, Any]]:
    file_path = Path(path)

    if not file_path.exists():
        raise FileNotFoundError(f"No existe el archivo: {file_path}")

    rows = []

    with file_path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                document = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"JSON inválido en línea {line_number}: {error}"
                ) from error

            document["_line_number"] = line_number
            rows.append(document)

    return rows


def metadata_value(metadata: dict[str, Any], key: str, default: Any = None) -> Any:
    value = metadata.get(key, default)

    if value == "":
        return default

    return value


def resolve_product_id(document: dict[str, Any]) -> str:
    metadata = document.get("metadata", {})

    product_id = (
        metadata.get("product_id")
        or metadata.get("source_id")
        or metadata.get("id")
        or document.get("product_id")
        or document.get("source_id")
        or document.get("id")
    )

    if not product_id:
        line_number = document.get("_line_number", "desconocida")
        raise ValueError(f"Producto sin identificador en línea {line_number}.")

    return str(product_id)


def validate_documents(documents: list[dict[str, Any]]) -> None:
    seen_product_ids = set()

    for document in documents:
        product_id = resolve_product_id(document)
        embedding_text = document.get("text", "")

        if not embedding_text:
            line_number = document.get("_line_number", "desconocida")
            raise ValueError(
                f"Producto {product_id} sin texto para embedding en línea {line_number}."
            )

        if product_id in seen_product_ids:
            raise ValueError(f"Producto duplicado en JSONL: {product_id}")

        seen_product_ids.add(product_id)


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE EXTENSION IF NOT EXISTS vector;
            CREATE EXTENSION IF NOT EXISTS pg_trgm;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS product_type TEXT;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS product_family TEXT;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS search_aliases TEXT;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS canonical_terms TEXT;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS normalized_title TEXT;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS embedding_text TEXT;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS search_text TEXT;

            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS rag_text TEXT;
            """
        )

    conn.commit()


def get_table_columns(conn: psycopg.Connection, table_name: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s;
            """,
            (table_name,),
        )

        rows = cur.fetchall()

    return {row[0] for row in rows}


def reset_catalog(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE product_embeddings;")
        cur.execute("TRUNCATE TABLE products CASCADE;")

    conn.commit()


def insert_product_row(
    conn: psycopg.Connection,
    document: dict[str, Any],
) -> None:
    metadata = document.get("metadata", {})
    product_id = resolve_product_id(document)
    embedding_text = document.get("text", "")
    rag_text = metadata_value(metadata, "rag_text", "")
    search_text = metadata_value(metadata, "search_text", "")

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO products (
                product_id,
                title,
                categoria_web,
                subcategoria_chatbot,
                precio,
                url,
                uso_detectado,
                especificaciones_detectadas,
                rag_text,
                active,
                product_type,
                product_family,
                search_aliases,
                canonical_terms,
                normalized_title,
                embedding_text,
                search_text
            )
            VALUES (
                %(product_id)s,
                %(title)s,
                %(categoria_web)s,
                %(subcategoria_chatbot)s,
                %(precio)s,
                %(url)s,
                %(uso_detectado)s,
                %(especificaciones_detectadas)s,
                %(rag_text)s,
                TRUE,
                %(product_type)s,
                %(product_family)s,
                %(search_aliases)s,
                %(canonical_terms)s,
                %(normalized_title)s,
                %(embedding_text)s,
                %(search_text)s
            )
            ON CONFLICT (product_id) DO UPDATE SET
                title = EXCLUDED.title,
                categoria_web = EXCLUDED.categoria_web,
                subcategoria_chatbot = EXCLUDED.subcategoria_chatbot,
                precio = EXCLUDED.precio,
                url = EXCLUDED.url,
                uso_detectado = EXCLUDED.uso_detectado,
                especificaciones_detectadas = EXCLUDED.especificaciones_detectadas,
                rag_text = EXCLUDED.rag_text,
                active = TRUE,
                product_type = EXCLUDED.product_type,
                product_family = EXCLUDED.product_family,
                search_aliases = EXCLUDED.search_aliases,
                canonical_terms = EXCLUDED.canonical_terms,
                normalized_title = EXCLUDED.normalized_title,
                embedding_text = EXCLUDED.embedding_text,
                search_text = EXCLUDED.search_text;
            """,
            {
                "product_id": product_id,
                "title": metadata_value(metadata, "title", ""),
                "categoria_web": metadata_value(metadata, "categoria_web", ""),
                "subcategoria_chatbot": metadata_value(
                    metadata,
                    "subcategoria_chatbot",
                    "",
                ),
                "precio": metadata_value(metadata, "precio"),
                "url": metadata_value(metadata, "url", ""),
                "uso_detectado": metadata_value(metadata, "uso_detectado", ""),
                "especificaciones_detectadas": metadata_value(
                    metadata,
                    "especificaciones_detectadas",
                    "",
                ),
                "rag_text": rag_text,
                "product_type": metadata_value(metadata, "product_type", ""),
                "product_family": metadata_value(metadata, "product_family", ""),
                "search_aliases": metadata_value(metadata, "search_aliases", ""),
                "canonical_terms": metadata_value(metadata, "canonical_terms", ""),
                "normalized_title": metadata_value(metadata, "normalized_title", ""),
                "embedding_text": embedding_text,
                "search_text": search_text,
            },
        )


def insert_embedding_row(
    conn: psycopg.Connection,
    product_id: str,
    embedding_text: str,
    embedding: list[float],
    product_embedding_columns: set[str],
) -> None:
    insert_columns = [
        "product_id",
        "embedding",
    ]

    values_sql = [
        "%(product_id)s",
        "%(embedding)s::vector",
    ]

    params = {
        "product_id": product_id,
        "embedding": to_pgvector_literal(embedding),
    }

    update_assignments = [
        "embedding = EXCLUDED.embedding",
    ]

    if "embedding_model" in product_embedding_columns:
        insert_columns.append("embedding_model")
        values_sql.append("%(embedding_model)s")
        params["embedding_model"] = EMBEDDING_MODEL
        update_assignments.append("embedding_model = EXCLUDED.embedding_model")

    if "model" in product_embedding_columns:
        insert_columns.append("model")
        values_sql.append("%(model)s")
        params["model"] = EMBEDDING_MODEL
        update_assignments.append("model = EXCLUDED.model")

    if "content_hash" in product_embedding_columns:
        insert_columns.append("content_hash")
        values_sql.append("%(content_hash)s")
        params["content_hash"] = get_content_hash(embedding_text)
        update_assignments.append("content_hash = EXCLUDED.content_hash")

    if "embedding_provider" in product_embedding_columns:
        insert_columns.append("embedding_provider")
        values_sql.append("%(embedding_provider)s")
        params["embedding_provider"] = "ollama"
        update_assignments.append("embedding_provider = EXCLUDED.embedding_provider")

    if "provider" in product_embedding_columns:
        insert_columns.append("provider")
        values_sql.append("%(provider)s")
        params["provider"] = "ollama"
        update_assignments.append("provider = EXCLUDED.provider")

    if "updated_at" in product_embedding_columns:
        update_assignments.append("updated_at = CURRENT_TIMESTAMP")

    sql = f"""
        INSERT INTO product_embeddings (
            {", ".join(insert_columns)}
        )
        VALUES (
            {", ".join(values_sql)}
        )
        ON CONFLICT (product_id) DO UPDATE SET
            {", ".join(update_assignments)};
    """

    with conn.cursor() as cur:
        cur.execute(sql, params)


def insert_product(
    conn: psycopg.Connection,
    document: dict[str, Any],
    embedding: list[float],
    product_embedding_columns: set[str],
) -> None:
    product_id = resolve_product_id(document)
    embedding_text = document.get("text", "")

    insert_product_row(conn, document)
    insert_embedding_row(
        conn,
        product_id,
        embedding_text,
        embedding,
        product_embedding_columns,
    )


def print_catalog_preview(documents: list[dict[str, Any]]) -> None:
    print("Primeros productos detectados:")

    for document in documents[:5]:
        metadata = document.get("metadata", {})
        product_id = resolve_product_id(document)
        title = metadata.get("title", "sin_titulo")
        product_type = metadata.get("product_type", "sin_tipo")
        product_family = metadata.get("product_family", "sin_familia")

        print(f"- {product_id} | {product_type} | {product_family} | {title}")


def print_embedding_table_info(product_embedding_columns: set[str]) -> None:
    columns = ", ".join(sorted(product_embedding_columns))
    print(f"Columnas detectadas en product_embeddings: {columns}")

    if "embedding_model" in product_embedding_columns:
        print(f"Se usará embedding_model = {EMBEDDING_MODEL}")

    if "content_hash" in product_embedding_columns:
        print("Se calculará content_hash con sha256(text).")


def main() -> None:
    documents = load_jsonl(CATALOG_FILE)
    validate_documents(documents)

    print(f"Archivo: {CATALOG_FILE}")
    print(f"Documentos a indexar: {len(documents)}")
    print(f"Modelo de embeddings: {EMBEDDING_MODEL}")
    print_catalog_preview(documents)

    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        ensure_schema(conn)

        product_embedding_columns = get_table_columns(conn, "product_embeddings")
        print_embedding_table_info(product_embedding_columns)

        reset_catalog(conn)

        for index, document in enumerate(documents, start=1):
            metadata = document.get("metadata", {})
            product_id = resolve_product_id(document)
            title = metadata.get("title", "sin_titulo")
            embedding_text = document.get("text", "")

            embedding = get_embedding(embedding_text)
            insert_product(conn, document, embedding, product_embedding_columns)

            if index % BATCH_SIZE == 0:
                conn.commit()
                print(f"Indexados {index}/{len(documents)} productos...")

            print(f"[{index}/{len(documents)}] {product_id} | {title}")

        conn.commit()

    print("Reindexación finalizada correctamente.")


if __name__ == "__main__":
    main()
