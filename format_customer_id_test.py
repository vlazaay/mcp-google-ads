"""Test format_customer_id from the creavy_ads package."""

from creavy_ads.auth import format_customer_id


def test_format_customer_id():
    """Test the format_customer_id function with various input formats."""
    test_cases = [
        # Regular ID
        ("9873186703", "9873186703"),
        # ID with dashes
        ("987-318-6703", "9873186703"),
        # ID with quotes
        ('"9873186703"', "9873186703"),
        # ID with escaped quotes
        ('\"9873186703\"', "9873186703"),
        # ID with leading zeros that exceed 10 digits - should preserve only last 10
        ("0009873186703", "0009873186703"),
        # Short ID that needs padding
        ("12345", "0000012345"),
        # ID with other non-digit characters
        ("{9873186703}", "9873186703"),
    ]

    print("\n=== Testing format_customer_id with various formats ===")
    for input_id, expected in test_cases:
        result = format_customer_id(input_id)
        print(f"Input: {input_id}")
        print(f"Result: {result}")
        print(f"Expected: {expected}")
        print(f"Test {'PASSED' if result == expected else 'FAILED'}")
        print("-" * 50)


if __name__ == "__main__":
    # Run format_customer_id tests
    test_format_customer_id()
