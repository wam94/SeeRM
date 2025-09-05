"""
CSV parsing and processing utilities for SeeRM application.

Provides robust CSV parsing with validation, normalization, and error handling.
"""

import io
import json
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import structlog

from app.core.exceptions import CSVParsingError, ValidationError
from app.core.models import AccountMovement, Company, DigestStats

logger = structlog.get_logger(__name__)


class CSVProcessor:
    """
    Centralized CSV processing with validation and normalization.
    """

    def __init__(self, strict_validation: bool = True):
        self.strict_validation = strict_validation

    def normalize_column_names(self, df: pd.DataFrame) -> Dict[str, str]:
        """
        Create mapping of normalized column names to original names.

        Args:
            df: Input DataFrame

        Returns:
            Dict mapping lowercase, stripped column names to originals
        """
        return {c.lower().strip(): c for c in df.columns}

    def safe_string_conversion(self, value: Any) -> Optional[str]:
        """
        Safely convert value to string, handling NaN and None.

        Args:
            value: Value to convert

        Returns:
            String representation or None for null values
        """
        if value is None:
            return None
        if hasattr(value, "__name__") and value.__name__ == "nan":
            return None
        if str(value).lower() in ("nan", "none", ""):
            return None
        return str(value).strip()

    def parse_numeric_string(self, value: Optional[pd.Series]) -> Optional[pd.Series]:
        """
        Parse numeric values from strings with currency symbols and commas.

        Args:
            value: Series to parse

        Returns:
            Parsed numeric series or None
        """
        if value is None:
            return None

        # Convert to string and clean
        cleaned = (
            value.astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("$", "", regex=False)
            .str.strip()
        )

        return pd.to_numeric(cleaned, errors="coerce")

    def parse_percentage_string(self, value: Optional[pd.Series]) -> Optional[pd.Series]:
        """
        Parse percentage values from strings.

        Args:
            value: Series to parse

        Returns:
            Parsed percentage series or None
        """
        if value is None:
            return None

        # Remove % symbol and convert to numeric
        cleaned = value.astype(str).str.replace("%", "", regex=False).str.strip()

        return pd.to_numeric(cleaned, errors="coerce")

    def parse_json_field(self, value: Any) -> List[str]:
        """
        Parse JSON field that might be a string or actual JSON.

        Args:
            value: Value to parse (string or list)

        Returns:
            List of parsed values
        """
        if pd.isna(value) or value is None:
            return []

        if isinstance(value, list):
            return [str(item).strip().strip('"') for item in value if item]

        if isinstance(value, str):
            # Try to parse as JSON first
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(item).strip().strip('"') for item in parsed if item]
            except (json.JSONDecodeError, ValueError):
                # Fallback to comma-separated parsing
                return [item.strip().strip('"') for item in value.split(",") if item.strip()]

        return [str(value).strip().strip('"')] if value else []

    def validate_required_columns(self, df: pd.DataFrame, required_columns: List[str]) -> None:
        """
        Validate that required columns are present.

        Args:
            df: DataFrame to validate
            required_columns: List of required column names (case-insensitive)

        Raises:
            ValidationError: If required columns are missing
        """
        column_mapping = self.normalize_column_names(df)
        missing_columns = []

        for required_col in required_columns:
            if required_col.lower() not in column_mapping:
                missing_columns.append(required_col)

        if missing_columns:
            raise ValidationError(
                f"Missing required columns: {missing_columns}. "
                f"Available columns: {list(df.columns)}"
            )

    def parse_companies_csv(self, df: pd.DataFrame) -> List[Company]:
        """
        Parse CSV data into Company objects.

        Args:
            df: DataFrame with company data

        Returns:
            List of Company objects

        Raises:
            CSVParsingError: On parsing errors
            ValidationError: On validation errors
        """
        try:
            # Validate basic structure
            if df.empty:
                if self.strict_validation:
                    raise ValidationError("CSV data is empty")
                else:
                    logger.warning("Empty CSV data - returning empty list")
                    return []

            # Normalize column names
            cols = self.normalize_column_names(df)

            # Helper function to get column value safely
            def get_column(col_name: str) -> Optional[pd.Series]:
                return df[cols[col_name]] if col_name in cols else None

            companies = []

            for index, row in df.iterrows():
                try:
                    # Extract basic company information
                    callsign = self.safe_string_conversion(row.get(cols.get("callsign")))
                    if not callsign:
                        if self.strict_validation:
                            raise ValidationError(f"Row {index}: callsign is required")
                        continue

                    # Parse beneficial owners (JSON field)
                    owners_raw = row.get(cols.get("beneficial_owners"))
                    beneficial_owners = self.parse_json_field(owners_raw)

                    # Create company object
                    company = Company(
                        callsign=callsign.lower(),
                        dba=self.safe_string_conversion(row.get(cols.get("dba"))),
                        website=self.safe_string_conversion(row.get(cols.get("website"))),
                        domain_root=self.safe_string_conversion(row.get(cols.get("domain_root"))),
                        blog_url=self.safe_string_conversion(row.get(cols.get("blog_url"))),
                        beneficial_owners=beneficial_owners,
                        aka_names=self.safe_string_conversion(row.get(cols.get("aka_names"))),
                        industry_tags=self.safe_string_conversion(
                            row.get(cols.get("industry_tags"))
                        ),
                        # Status flags
                        is_new_account=bool(row.get(cols.get("is_new_account"), False)),
                        is_removed_account=bool(row.get(cols.get("is_removed_account"), False)),
                        dba_changed=bool(row.get(cols.get("dba_changed"), False)),
                        website_changed=bool(row.get(cols.get("website_changed"), False)),
                        owners_changed=bool(row.get(cols.get("owners_changed"), False)),
                        balance_changed=bool(row.get(cols.get("balance_changed"), False)),
                        # Financial data
                        curr_balance=pd.to_numeric(
                            row.get(cols.get("curr_balance")), errors="coerce"
                        ),
                        prev_balance=pd.to_numeric(
                            row.get(cols.get("prev_balance")), errors="coerce"
                        ),
                        balance_delta=pd.to_numeric(
                            row.get(cols.get("balance_delta")), errors="coerce"
                        ),
                        balance_pct_delta_pct=pd.to_numeric(
                            row.get(cols.get("balance_pct_delta_pct")), errors="coerce"
                        ),
                        # Product changes
                        product_flips_json=self.safe_string_conversion(
                            row.get(cols.get("product_flips_json"))
                        ),
                    )

                    # Calculate any_change if not provided
                    if "any_change" not in cols:
                        company.any_change = (
                            company.is_new_account
                            or company.is_removed_account
                            or company.dba_changed
                            or company.website_changed
                            or company.owners_changed
                            or company.balance_changed
                        )
                    else:
                        company.any_change = bool(row.get(cols.get("any_change"), False))

                    companies.append(company)

                except Exception as e:
                    error_msg = f"Failed to parse row {index}: {e}"
                    logger.error("Row parsing error", row_index=index, error=str(e))

                    if self.strict_validation:
                        raise CSVParsingError(error_msg)
                    else:
                        # Log and continue
                        logger.warning("Skipping invalid row", row_index=index, error=str(e))
                        continue

            logger.info("CSV parsing completed", total_rows=len(df), valid_companies=len(companies))

            return companies

        except Exception as e:
            if isinstance(e, (CSVParsingError, ValidationError)):
                raise

            error_msg = f"Unexpected error parsing companies CSV: {e}"
            logger.error("CSV parsing failed", error=str(e))
            raise CSVParsingError(error_msg)

    def calculate_digest_data(self, companies: List[Company], top_n: int = 15) -> Dict[str, Any]:
        """
        Calculate digest statistics and movement data from companies.

        Args:
            companies: List of Company objects
            top_n: Number of top movers to include

        Returns:
            Dictionary with digest data
        """
        try:
            # Calculate statistics
            stats = DigestStats(
                total_accounts=len(companies),
                changed_accounts=sum(1 for c in companies if c.any_change),
                new_accounts=sum(1 for c in companies if c.is_new_account),
                removed_accounts=sum(1 for c in companies if c.is_removed_account),
                total_product_flips=0,  # Will be calculated from product flips data
            )

            # Calculate top movers by percentage
            companies_with_pct = [
                c
                for c in companies
                if c.balance_pct_delta_pct is not None and pd.notna(c.balance_pct_delta_pct)
            ]

            # Top gainers (positive percentage change)
            gainers = [c for c in companies_with_pct if c.balance_pct_delta_pct > 0]
            gainers.sort(key=lambda x: x.balance_pct_delta_pct, reverse=True)

            top_pct_gainers = [
                AccountMovement(
                    callsign=c.callsign,
                    percentage_change=c.balance_pct_delta_pct,
                    balance_delta=c.balance_delta,
                )
                for c in gainers[:top_n]
            ]

            # Top losers (negative percentage change)
            losers = [c for c in companies_with_pct if c.balance_pct_delta_pct < 0]
            losers.sort(key=lambda x: x.balance_pct_delta_pct)

            top_pct_losers = [
                AccountMovement(
                    callsign=c.callsign,
                    percentage_change=c.balance_pct_delta_pct,
                    balance_delta=c.balance_delta,
                )
                for c in losers[:top_n]
            ]

            # Parse product flips from JSON columns
            product_starts = []
            product_stops = []
            
            for company in companies:
                if hasattr(company, 'product_flips_json') and company.product_flips_json:
                    try:
                        import json
                        # Handle the case where product_flips_json might be a string
                        if isinstance(company.product_flips_json, str) and company.product_flips_json.strip():
                            flips = json.loads(company.product_flips_json)
                        elif isinstance(company.product_flips_json, list):
                            flips = company.product_flips_json
                        else:
                            continue
                            
                        for flip in flips:
                            if isinstance(flip, dict) and 'product' in flip:
                                product_name = flip.get('product', 'Unknown')
                                from_status = flip.get('from', 0)
                                to_status = flip.get('to', 0)
                                
                                if from_status == 0 and to_status == 1:
                                    # Started using product
                                    product_starts.append({
                                        'callsign': company.callsign,
                                        'product': product_name
                                    })
                                elif from_status == 1 and to_status == 0:
                                    # Stopped using product
                                    product_stops.append({
                                        'callsign': company.callsign,
                                        'product': product_name
                                    })
                    except (json.JSONDecodeError, AttributeError, KeyError, TypeError) as e:
                        logger.debug("Failed to parse product flips for company", 
                                   callsign=getattr(company, 'callsign', 'unknown'), 
                                   error=str(e))
                        continue
            
            # Update stats with total product flips
            stats.total_product_flips = len(product_starts) + len(product_stops)

            return {
                "stats": stats.model_dump(),
                "top_pct_gainers": [g.model_dump() for g in top_pct_gainers],
                "top_pct_losers": [l.model_dump() for l in top_pct_losers],
                "product_starts": product_starts,
                "product_stops": product_stops,
            }

        except Exception as e:
            error_msg = f"Failed to calculate digest data: {e}"
            logger.error("Digest calculation failed", error=str(e))
            raise CSVParsingError(error_msg)

    def extract_new_callsigns(self, companies: List[Company]) -> List[str]:
        """
        Extract callsigns of new accounts.

        Args:
            companies: List of Company objects

        Returns:
            List of new account callsigns
        """
        new_callsigns = [c.callsign for c in companies if c.is_new_account and c.callsign]

        logger.info(
            "New callsigns extracted",
            count=len(new_callsigns),
            callsigns=new_callsigns[:10],  # Log first 10 for debugging
        )

        return new_callsigns

    def validate_csv_data(self, df: pd.DataFrame) -> Tuple[bool, List[str]]:
        """
        Validate CSV data structure and content.

        Args:
            df: DataFrame to validate

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        try:
            # Check if DataFrame is empty
            if df.empty:
                errors.append("CSV data is empty")
                return False, errors

            # Check for basic required columns
            cols = self.normalize_column_names(df)

            if "callsign" not in cols:
                errors.append("Missing required column: callsign")

            # Check for data quality issues
            if "callsign" in cols:
                null_callsigns = df[cols["callsign"]].isna().sum()
                if null_callsigns > 0:
                    errors.append(f"Found {null_callsigns} rows with null callsigns")

            # Check for duplicate callsigns
            if "callsign" in cols:
                duplicates = df[cols["callsign"]].duplicated().sum()
                if duplicates > 0:
                    errors.append(f"Found {duplicates} duplicate callsigns")

            # Validate numeric columns if present
            numeric_columns = [
                "curr_balance",
                "prev_balance",
                "balance_delta",
                "balance_pct_delta_pct",
            ]
            for col in numeric_columns:
                if col in cols:
                    try:
                        pd.to_numeric(df[cols[col]], errors="coerce")
                    except Exception as e:
                        errors.append(f"Invalid numeric data in column {col}: {e}")

            # Validate boolean columns if present
            boolean_columns = ["is_new_account", "is_removed_account", "any_change"]
            for col in boolean_columns:
                if col in cols:
                    unique_values = df[cols[col]].dropna().unique()
                    valid_boolean_values = {
                        True,
                        False,
                        0,
                        1,
                        "true",
                        "false",
                        "True",
                        "False",
                        "0",
                        "1",
                    }
                    invalid_values = set(unique_values) - valid_boolean_values
                    if invalid_values:
                        errors.append(f"Invalid boolean values in column {col}: {invalid_values}")

            is_valid = len(errors) == 0

            logger.info(
                "CSV validation completed",
                is_valid=is_valid,
                error_count=len(errors),
                rows=len(df),
                columns=len(df.columns),
            )

            return is_valid, errors

        except Exception as e:
            errors.append(f"Validation error: {e}")
            logger.error("CSV validation failed", error=str(e))
            return False, errors


def parse_csv_data(
    csv_data: bytes, strict_validation: bool = True
) -> Tuple[List[Company], Dict[str, Any]]:
    """
    Parse CSV data from bytes into companies and digest data.

    Args:
        csv_data: Raw CSV data bytes
        strict_validation: Whether to use strict validation

    Returns:
        Tuple of (companies_list, digest_data_dict)

    Raises:
        CSVParsingError: On parsing errors
        ValidationError: On validation errors
    """
    try:
        # Parse CSV into DataFrame
        df = pd.read_csv(io.BytesIO(csv_data))

        # Create processor and validate
        processor = CSVProcessor(strict_validation=strict_validation)
        is_valid, errors = processor.validate_csv_data(df)

        if not is_valid and strict_validation:
            raise ValidationError(f"CSV validation failed: {'; '.join(errors)}")
        elif not is_valid:
            logger.warning("CSV validation warnings", errors=errors)

        # Parse companies
        companies = processor.parse_companies_csv(df)

        # Calculate digest data
        digest_data = processor.calculate_digest_data(companies)

        logger.info(
            "CSV processing completed successfully",
            companies_count=len(companies),
            digest_stats=digest_data.get("stats", {}),
        )

        return companies, digest_data

    except Exception as e:
        if isinstance(e, (CSVParsingError, ValidationError)):
            raise

        error_msg = f"Unexpected error parsing CSV data: {e}"
        logger.error("CSV processing failed", error=str(e))
        raise CSVParsingError(error_msg)


def parse_csv_file(
    file_path: str, strict_validation: bool = True
) -> Tuple[List[Company], Dict[str, Any]]:
    """
    Parse CSV file into companies and digest data.

    Args:
        file_path: Path to CSV file
        strict_validation: Whether to use strict validation

    Returns:
        Tuple of (companies_list, digest_data_dict)

    Raises:
        CSVParsingError: On parsing errors
        ValidationError: On validation errors
    """
    try:
        with open(file_path, "rb") as f:
            csv_data = f.read()

        return parse_csv_data(csv_data, strict_validation)

    except FileNotFoundError:
        raise ValidationError(f"CSV file not found: {file_path}")
    except Exception as e:
        if isinstance(e, (CSVParsingError, ValidationError)):
            raise

        error_msg = f"Failed to read CSV file {file_path}: {e}"
        logger.error("File reading failed", file_path=file_path, error=str(e))
        raise CSVParsingError(error_msg)
