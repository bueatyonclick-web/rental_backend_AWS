import uuid

import os

from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import models
import uuid
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


# ============== VENDOR MODEL ==============
class Vendor(models.Model):
    """
    Vendor model - Created and managed by admin only
    Vendors cannot self-register
    """
    id = models.AutoField(primary_key=True)
    vendor_id = models.CharField(max_length=50, unique=True, help_text="Unique vendor ID (e.g., VEN001)")
    name = models.CharField(max_length=200, help_text="Vendor business name")
    email = models.EmailField(unique=True, help_text="Vendor email for login")
    phone = models.CharField(max_length=15, help_text="Vendor contact number")
    password = models.CharField(max_length=255, help_text="Hashed password")

    # Business Information
    business_address = models.TextField(blank=True, null=True)
    gst_number = models.CharField(max_length=15, blank=True, null=True)

    # Status
    is_active = models.BooleanField(default=True, help_text="Vendor account status")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vendor"
        verbose_name_plural = "Vendors"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.vendor_id} - {self.name}"

    def set_password(self, raw_password):
        """Set hashed password"""
        self.password = make_password(raw_password)

    def save(self, *args, **kwargs):
        # Auto-generate vendor_id if not provided
        if not self.vendor_id:
            last_vendor = Vendor.objects.all().order_by('-id').first()
            if last_vendor:
                last_id = int(last_vendor.vendor_id.replace('VEN', ''))
                self.vendor_id = f'VEN{str(last_id + 1).zfill(3)}'
            else:
                self.vendor_id = 'VEN001'
        super().save(*args, **kwargs)


# ============== VENDOR TOKEN MODEL ==============
class VendorToken(models.Model):
    """
    Authentication tokens for vendors
    """
    token = models.CharField(max_length=500, unique=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="tokens_set")
    fcmtoken = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Vendor Token"
        verbose_name_plural = "Vendor Tokens"

    def __str__(self):
        return f"{self.vendor.vendor_id} - Token"


class Otp(models.Model):
    phone = models.CharField(max_length=10)
    otp = models.IntegerField()
    validity = models.DateTimeField()
    verified = models.BooleanField(default=False)

    def __str__(self):
        return self.phone


class Category(models.Model):
    GENDER_MALE = 'male'
    GENDER_FEMALE = 'female'
    GENDER_CHOICES = [
        (GENDER_MALE, 'Male'),
        (GENDER_FEMALE, 'Female'),
    ]
    name = models.CharField(max_length=50)
    position = models.IntegerField(default=0)
    image = models.ImageField(upload_to='categories/')
    gender = models.CharField(
        max_length=10,
        choices=GENDER_CHOICES,
        default=GENDER_FEMALE,
        help_text='Category for Male or Female (used in Rents section)',
    )

    def __str__(self):
        return self.name


class Slide(models.Model):
    position = models.IntegerField(default=0)
    image = models.ImageField(upload_to='categories/')


class HomeBanner(models.Model):
    """
    Dynamic home page banners/templates managed from admin.
    Shown on customer app home between Search Bar and Rent/Services toggle.
    """
    REDIRECT_PRODUCT = 'product'
    REDIRECT_CATEGORY = 'category'
    REDIRECT_EXTERNAL = 'external_link'
    REDIRECT_CHOICES = [
        (REDIRECT_PRODUCT, 'Product'),
        (REDIRECT_CATEGORY, 'Category'),
        (REDIRECT_EXTERNAL, 'External Link'),
    ]

    title = models.CharField(max_length=200, blank=True, null=True)
    image = models.ImageField(upload_to='home_banners/')
    redirect_type = models.CharField(
        max_length=20,
        choices=REDIRECT_CHOICES,
        default=REDIRECT_CATEGORY,
        help_text='product, category, or external_link',
    )
    redirect_value = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        help_text='Product ID, category slug, or full URL for external_link',
    )
    display_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(blank=True, null=True, help_text='Soft delete')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'id']
        verbose_name = 'Home Banner'
        verbose_name_plural = 'Home Banners'

    def __str__(self):
        return self.title or f'Banner #{self.id}'


class HomeGenderTileImage(models.Model):
    """
    Single-row settings for Male/Female category tile images on the home page.
    Upload images in admin; app shows these on the Rents section tiles.
    """
    male_tile_image = models.ImageField(
        upload_to='home_tiles/',
        blank=True,
        null=True,
        help_text='Image for the Male category tile on home page',
    )
    female_tile_image = models.ImageField(
        upload_to='home_tiles/',
        blank=True,
        null=True,
        help_text='Image for the Female category tile on home page',
    )

    class Meta:
        verbose_name = 'Home Male/Female tile images'
        verbose_name_plural = 'Home Male/Female tile images'

    def __str__(self):
        return 'Male & Female home tile images'


# Add after your Product model

class ProductBooking(models.Model):
    """
    Track booked dates for products that require date selection
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey('Product', on_delete=models.CASCADE, related_name='bookings')
    product_option = models.ForeignKey('ProductOption', on_delete=models.CASCADE, related_name='bookings', null=True,
                                       blank=True)
    booking_date = models.DateField(help_text="Date when product is booked")
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='product_bookings')
    order = models.ForeignKey('Order', on_delete=models.CASCADE, related_name='product_bookings', null=True, blank=True)
    quantity_booked = models.IntegerField(default=1, help_text="Number of items booked for this date")

    # NEW RENTAL FIELDS
    rental_type = models.CharField(
        max_length=10,
        choices=[('rent', 'Rent'), ('buy', 'Buy')],
        default='rent',
    )
    rental_duration = models.CharField(
        max_length=20,
        blank=True,
        null=True,
    )
    rental_end_date = models.DateField(
        null=True,
        blank=True,
        help_text="End date for rental period"
    )

    status = models.CharField(
        max_length=20,
        choices=[
            ('PENDING', 'Pending'),
            ('CONFIRMED', 'Confirmed'),
            ('CANCELLED', 'Cancelled'),
            ('COMPLETED', 'Completed'),
        ],
        default='PENDING'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Product Booking"
        verbose_name_plural = "Product Bookings"
        ordering = ['-created_at']

    def __str__(self):
        rental_info = f"{self.rental_type} - {self.rental_duration}" if self.rental_type == 'rent' else "Purchase"
        return f"{self.product.title} - {self.booking_date} ({rental_info})"

class CartItem(models.Model):
    """
    Intermediate model to store cart items with dates and rental info
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='cart_items')
    product_option = models.ForeignKey('ProductOption', on_delete=models.CASCADE)
    quantity = models.IntegerField(default=1)
    selected_date = models.DateField(null=True, blank=True, help_text="Selected booking date")

    # NEW RENTAL FIELDS
    rental_type = models.CharField(
        max_length=10,
        choices=[('rent', 'Rent'), ('buy', 'Buy')],
        default='rent',
        help_text="Whether customer is renting or buying"
    )
    rental_duration = models.CharField(
        max_length=20,
        default='',  # Ã¢Å“â€¦ Changed from '1_day' to empty string
        blank=True,  # Ã¢Å“â€¦ Added blank=True
        help_text="Rental duration (e.g., 1_day, 7_days, 30_days)"
    )
    rental_price = models.IntegerField(
        default=0,
        help_text="Calculated rental price based on duration"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Ã¢Å“â€¦ REMOVED unique_together to avoid issues with None values
        # We'll handle uniqueness in the view logic instead
        pass

    def __str__(self):
        date_str = f" - {self.selected_date}" if self.selected_date else ""
        rental_str = f" ({self.rental_type} - {self.rental_duration})" if self.rental_type == 'rent' else " (Buy)"
        return f"{self.user.email} - {self.product_option}{date_str}{rental_str}"


class UserAddress(models.Model):
    """Multiple addresses per user"""
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='addresses')
    type = models.CharField(max_length=20, default='Home')  # Home, Work, Other
    name = models.CharField(max_length=100)
    address = models.TextField(max_length=1000)
    contact_no = models.CharField(max_length=15)
    pincode = models.IntegerField(blank=True, null=True)
    district = models.CharField(max_length=500, blank=True)
    state = models.CharField(max_length=500, blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_default', '-created_at']

    def __str__(self):
        return f"{self.name} - {self.type}"

    def save(self, *args, **kwargs):
        # Ensure only one default address per user
        if self.is_default:
            UserAddress.objects.filter(user=self.user, is_default=True).update(is_default=False)
        super().save(*args, **kwargs)



class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='products_set', null=True, blank=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='products_set')
    title = models.CharField(max_length=500)
    description = models.TextField(max_length=100000)
    price = models.IntegerField(default=0)
    offer_price = models.IntegerField(default=0)
    delivery_charge = models.IntegerField(default=0)

    # Date booking fields
    requires_date_selection = models.BooleanField(
        default=True,
        help_text="Whether this product requires date selection for booking"
    )
    max_bookings_per_date = models.IntegerField(
        default=10,
        help_text="Maximum number of bookings allowed per date (0 = unlimited)"
    )

    # Ã¢Å“â€¦ NEW: Rental Pricing Fields
    # Rent pricing for different durations
    rent_price_1_day = models.IntegerField(default=0, help_text="Rental price for 1 day")
    rent_price_2_days = models.IntegerField(default=0, help_text="Rental price for 2 days")
    rent_price_3_days = models.IntegerField(default=0, help_text="Rental price for 3 days")
    rent_price_7_days = models.IntegerField(default=0, help_text="Rental price for 7 days")
    rent_price_14_days = models.IntegerField(default=0, help_text="Rental price for 14 days")
    rent_price_30_days = models.IntegerField(default=0, help_text="Rental price for 30 days")

    # Buy pricing
    buy_price = models.IntegerField(default=0, help_text="Purchase price (if 0, uses regular price)")
    buy_offer_price = models.IntegerField(default=0, help_text="Purchase offer price (if 0, uses offer_price)")

    star_5 = models.IntegerField(default=0)
    star_4 = models.IntegerField(default=0)
    star_3 = models.IntegerField(default=0)
    star_2 = models.IntegerField(default=0)
    star_1 = models.IntegerField(default=0)
    cod = models.BooleanField(default=True)
    position = models.IntegerField(default=9999, help_text="Display order within category (lower = first)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    def get_rental_price(self, duration):
        """Get rental price for specific duration"""
        duration_fields = {
            '1_day': self.rent_price_1_day,
            '2_days': self.rent_price_2_days,
            '3_days': self.rent_price_3_days,
            '7_days': self.rent_price_7_days,
            '14_days': self.rent_price_14_days,
            '30_days': self.rent_price_30_days,
        }

        price = duration_fields.get(duration, 0)

        # Fallback to calculated price if not set
        if price == 0:
            base_price = self.offer_price if self.offer_price > 0 else self.price
            duration_multipliers = {
                '1_day': 1,
                '2_days': 2,
                '3_days': 2.7,
                '7_days': 6.3,
                '14_days': 11.9,
                '30_days': 24,
            }
            multiplier = duration_multipliers.get(duration, 1)
            price = int(base_price * multiplier)

        return price

    def get_buy_price(self):
        """Get purchase price"""
        if self.buy_price > 0:
            return self.buy_price
        return self.price

    def get_buy_offer_price(self):
        """Get purchase offer price"""
        if self.buy_offer_price > 0:
            return self.buy_offer_price
        return self.offer_price if self.offer_price > 0 else None


# models.py - Add rental pricing to ProductOption

class ProductOption(models.Model):
    """
    Product variant/option with individual pricing and stock management.
    Supports auto-calculation of rental prices based on offer price.

    PRICING FORMULA: Simple Linear Pricing
    - Base: option_offer_price
    - 1 Day: 1x base (â‚¹100 â†’ â‚¹100)
    - 2 Days: 2x base (â‚¹100 â†’ â‚¹200)
    - 3 Days: 3x base (â‚¹100 â†’ â‚¹300)
    - 7 Days: 7x base (â‚¹100 â†’ â‚¹700)
    - 14 Days: 14x base (â‚¹100 â†’ â‚¹1,400)
    - 30 Days: 30x base (â‚¹100 â†’ â‚¹3,000)
    """

    # ============== PRIMARY FIELDS ==============
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey('Product', on_delete=models.CASCADE, related_name='options_set')
    option = models.CharField(max_length=50, blank=True, help_text="Variant name (e.g., Size M, Color Red)")
    quantity = models.IntegerField(default=0, help_text="Available stock quantity")
    is_rent_available = models.BooleanField(default=True)
    is_buy_available = models.BooleanField(default=True)

    # ============== AUTO-CALCULATION CONTROL ==============
    auto_calculate_rental_prices = models.BooleanField(
        default=True,
        help_text="âœ¨ Automatically calculate all rental prices (base Ã— days)"
    )

    # ============== STANDARD PRICING (Override Product) ==============
    option_price = models.IntegerField(
        default=0,
        help_text="Option-specific price (0 = use product price)"
    )
    option_offer_price = models.IntegerField(
        default=0,
        help_text="Option-specific offer price (0 = use product offer_price) - BASE FOR AUTO-CALCULATION"
    )

    # ============== RENTAL PRICING (Per Duration) ==============
    option_rent_1_day = models.IntegerField(
        default=0,
        help_text="Rental price for 1 day (auto: base Ã— 1)"
    )
    option_rent_2_days = models.IntegerField(
        default=0,
        help_text="Rental price for 2 days (auto: base Ã— 2)"
    )
    option_rent_3_days = models.IntegerField(
        default=0,
        help_text="Rental price for 3 days (auto: base Ã— 3)"
    )
    option_rent_7_days = models.IntegerField(
        default=0,
        help_text="Rental price for 7 days (auto: base Ã— 7)"
    )
    option_rent_14_days = models.IntegerField(
        default=0,
        help_text="Rental price for 14 days (auto: base Ã— 14)"
    )
    option_rent_30_days = models.IntegerField(
        default=0,
        help_text="Rental price for 30 days (auto: base Ã— 30)"
    )

    # ============== PURCHASE PRICING ==============
    option_buy_price = models.IntegerField(
        default=0,
        help_text="Option-specific buy price (0 = use product buy_price) - Auto: base Ã— 50"
    )
    option_buy_offer_price = models.IntegerField(
        default=0,
        help_text="Option-specific buy offer (0 = use product buy_offer_price) - Auto: base Ã— 40"
    )



    class Meta:
        verbose_name = "Product Option"
        verbose_name_plural = "Product Options"
        ordering = ['product', 'option']

    def __str__(self):
        if self.option:
            return f"({self.option}) {self.product.title}"
        return f"{self.product.title}"

    # ============== SAVE METHOD WITH AUTO-CALCULATION ==============
    def save(self, *args, **kwargs):
        """
        ✅ CRITICAL: Force boolean conversion on EVERY save
        """
        # ✅ Force proper boolean type - handle ANY input
        if hasattr(self, 'is_rent_available'):
            # Handle None, empty string, 0, "false", etc.
            if self.is_rent_available in [None, '', 0, '0', 'false', 'False', 'FALSE']:
                self.is_rent_available = False
            elif self.is_rent_available in [1, '1', 'true', 'True', 'TRUE']:
                self.is_rent_available = True
            else:
                self.is_rent_available = bool(self.is_rent_available)
        else:
            self.is_rent_available = True  # Default

        if hasattr(self, 'is_buy_available'):
            if self.is_buy_available in [None, '', 0, '0', 'false', 'False', 'FALSE']:
                self.is_buy_available = False
            elif self.is_buy_available in [1, '1', 'true', 'True', 'TRUE']:
                self.is_buy_available = True
            else:
                self.is_buy_available = bool(self.is_buy_available)
        else:
            self.is_buy_available = True  # Default

        # ✅ Log what's being saved
        print(f"💾 Saving {self.option}:")
        print(f"   is_rent_available = {self.is_rent_available} ({type(self.is_rent_available).__name__})")
        print(f"   is_buy_available = {self.is_buy_available} ({type(self.is_buy_available).__name__})")

        # Call parent save
        super().save(*args, **kwargs)

        # ✅ Verify what was saved
        self.refresh_from_db()
        print(f"✅ Verified in DB: rent={self.is_rent_available}, buy={self.is_buy_available}")

    # ============== PRICING GETTER METHODS ==============

    def get_price(self):
        """
        Get effective price for this option.
        Returns option-specific price or falls back to product price.
        """
        if self.option_price > 0:
            return self.option_price
        return self.product.price if self.product else 0

    def get_offer_price(self):
        """
        Get effective offer price for this option.
        Returns option-specific offer or falls back to product offer_price.
        """
        if self.option_offer_price > 0:
            return self.option_offer_price
        return self.product.offer_price if self.product else 0

    def get_rental_price(self, duration):
        """
        Get rental price for specific duration.

        Args:
            duration (str): Duration key (e.g., '1_day', '7_days', '30_days')

        Returns:
            int: Rental price for the specified duration

        Priority:
        1. Custom option rental price (if set)
        2. Product-level rental price
        3. Auto-calculated price (base Ã— days)
        """
        # Map duration to option field
        option_prices = {
            '1_day': self.option_rent_1_day,
            '2_days': self.option_rent_2_days,
            '3_days': self.option_rent_3_days,
            '7_days': self.option_rent_7_days,
            '14_days': self.option_rent_14_days,
            '30_days': self.option_rent_30_days,
        }

        option_price = option_prices.get(duration, 0)

        # If option has custom price, use it
        if option_price > 0:
            return option_price

        # Otherwise, fallback to product pricing
        if self.product:
            product_price = self.product.get_rental_price(duration)
            if product_price > 0:
                return product_price

        # Last resort: calculate on the fly (linear pricing)
        base_price = self.get_offer_price() or self.get_price()
        duration_days = {
            '1_day': 1,
            '2_days': 2,
            '3_days': 3,
            '7_days': 7,
            '14_days': 14,
            '30_days': 30,
        }
        days = duration_days.get(duration, 1)
        return int(base_price * days)

    def get_buy_price(self):
        """
        Get purchase price for this option.

        Priority:
        1. Custom option buy price
        2. Product buy price
        3. Auto-calculated (base Ã— 50)
        """
        if self.option_buy_price > 0:
            return self.option_buy_price

        if self.product:
            product_buy = self.product.get_buy_price()
            if product_buy > 0:
                return product_buy

        # Auto-calculate: 50 days worth
        base_price = self.get_offer_price() or self.get_price()
        return int(base_price * 50)

    def get_buy_offer_price(self):
        """
        Get purchase offer price for this option.

        Priority:
        1. Custom option buy offer
        2. Product buy offer
        3. Auto-calculated (base Ã— 40)
        """
        if self.option_buy_offer_price > 0:
            return self.option_buy_offer_price

        if self.product:
            product_offer = self.product.get_buy_offer_price()
            if product_offer and product_offer > 0:
                return product_offer

        # Auto-calculate: 40 days worth
        base_price = self.get_offer_price() or self.get_price()
        return int(base_price * 40)

    def get_rental_pricing_dict(self):
        """
        Get complete rental pricing structure for API responses.

        Returns:
            dict: Complete pricing dictionary with rent and buy options
        """
        return {
            'rent': {
                '1_day': self.get_rental_price('1_day'),
                '2_days': self.get_rental_price('2_days'),
                '3_days': self.get_rental_price('3_days'),
                '7_days': self.get_rental_price('7_days'),
                '14_days': self.get_rental_price('14_days'),
                '30_days': self.get_rental_price('30_days'),
            },
            'buy': {
                'price': self.get_buy_price(),
                'offer_price': self.get_buy_offer_price(),
            }
        }

    # ============== VALIDATION ==============

    def clean(self):
        """
        Validate pricing logic before saving.
        """
        errors = {}

        # Validate that quantity is not negative
        if self.quantity < 0:
            errors['quantity'] = "Quantity cannot be negative"

        # Validate that prices are not negative
        price_fields = [
            'option_price', 'option_offer_price',
            'option_rent_1_day', 'option_rent_2_days', 'option_rent_3_days',
            'option_rent_7_days', 'option_rent_14_days', 'option_rent_30_days',
            'option_buy_price', 'option_buy_offer_price'
        ]

        for field in price_fields:
            value = getattr(self, field, 0)
            if value < 0:
                errors[field] = f"{field} cannot be negative"

        # Validate offer price is not greater than regular price (if both set)
        if self.option_price > 0 and self.option_offer_price > self.option_price:
            errors['option_offer_price'] = "Offer price cannot be greater than regular price"

        if errors:
            raise ValidationError(errors)

    # ============== UTILITY METHODS ==============

    def has_custom_pricing(self):
        """
        Check if this option has any custom pricing set.

        Returns:
            bool: True if any custom price is set, False otherwise
        """
        return any([
            self.option_price > 0,
            self.option_offer_price > 0,
            self.option_rent_1_day > 0,
            self.option_rent_2_days > 0,
            self.option_rent_3_days > 0,
            self.option_rent_7_days > 0,
            self.option_rent_14_days > 0,
            self.option_rent_30_days > 0,
            self.option_buy_price > 0,
            self.option_buy_offer_price > 0,
        ])

    def has_custom_rental_pricing(self):
        """Check if custom rental pricing is set"""
        return any([
            self.option_rent_1_day > 0,
            self.option_rent_2_days > 0,
            self.option_rent_3_days > 0,
            self.option_rent_7_days > 0,
            self.option_rent_14_days > 0,
            self.option_rent_30_days > 0,
        ])

    def has_custom_buy_pricing(self):
        """Check if custom buy pricing is set"""
        return self.option_buy_price > 0 or self.option_buy_offer_price > 0

    def is_in_stock(self):
        """Check if item is in stock"""
        return self.quantity > 0

    def get_stock_status(self):
        """
        Get human-readable stock status.

        Returns:
            str: Stock status message
        """
        if self.quantity == 0:
            return "Out of Stock"
        elif self.quantity < 10:
            return f"Low Stock ({self.quantity} left)"
        else:
            return f"In Stock ({self.quantity} available)"

    def calculate_savings(self, duration='1_day'):
        """
        Calculate savings compared to purchase price.

        Args:
            duration (str): Rental duration

        Returns:
            int: Amount saved by renting vs buying
        """
        rental_price = self.get_rental_price(duration)
        buy_price = self.get_buy_offer_price() or self.get_buy_price()
        return max(0, buy_price - rental_price)

    def get_price_per_day(self, duration='1_day'):
        """
        Calculate price per day for a given duration.

        Args:
            duration (str): Rental duration

        Returns:
            float: Price per day
        """
        price = self.get_rental_price(duration)

        duration_days = {
            '1_day': 1,
            '2_days': 2,
            '3_days': 3,
            '7_days': 7,
            '14_days': 14,
            '30_days': 30,
        }

        days = duration_days.get(duration, 1)
        return round(price / days, 2)

    def get_breakeven_point(self):
        """
        Calculate how many days of rental equals the purchase price.

        Returns:
            int: Number of days to break even
        """
        daily_rate = self.get_rental_price('1_day')
        buy_price = self.get_buy_offer_price() or self.get_buy_price()

        if daily_rate > 0:
            return int(buy_price / daily_rate)
        return 0

    def get_best_value_duration(self):
        """
        Since pricing is linear, all durations have the same per-day rate.

        Returns:
            tuple: (duration, price_per_day)
        """
        base_price = self.get_offer_price() or self.get_price()
        return '30_days', base_price  # All have same per-day rate

    # ============== ADMIN DISPLAY HELPERS ==============

    def get_pricing_summary(self):
        """
        Get a summary of pricing for admin display.

        Returns:
            dict: Pricing summary with all relevant prices
        """
        return {
            'has_custom': self.has_custom_pricing(),
            'base_price': self.get_offer_price() or self.get_price(),
            'standard_price': self.get_price(),
            'offer_price': self.get_offer_price(),
            'rent_1_day': self.get_rental_price('1_day'),
            'rent_7_days': self.get_rental_price('7_days'),
            'rent_30_days': self.get_rental_price('30_days'),
            'buy_price': self.get_buy_price(),
            'buy_offer': self.get_buy_offer_price(),
            'auto_calc_enabled': self.auto_calculate_rental_prices,
            'price_per_day': self.get_price_per_day('1_day'),
            'breakeven_days': self.get_breakeven_point(),
        }

    def get_pricing_source(self, price_type):
        """
        Determine the source of a specific price.

        Args:
            price_type (str): Type of price ('standard', 'rent_1_day', 'buy', etc.)

        Returns:
            str: 'option' or 'product' indicating the source
        """
        if price_type == 'standard':
            return 'option' if self.option_offer_price > 0 else 'product'
        elif price_type.startswith('rent_'):
            field_name = f'option_{price_type}'
            return 'option' if getattr(self, field_name, 0) > 0 else 'product'
        elif price_type == 'buy':
            return 'option' if self.option_buy_price > 0 else 'product'
        return 'unknown'

    # ============== METADATA ==============

    @property
    def display_name(self):
        """Get display-friendly name"""
        return str(self)

    @property
    def is_auto_priced(self):
        """Check if this option uses auto-calculated prices"""
        return self.auto_calculate_rental_prices and self.option_offer_price > 0

    @property
    def base_daily_rate(self):
        """Get the base daily rental rate"""
        return self.get_offer_price() or self.get_price()

    def get_absolute_url(self):
        """Get URL for this option (for use in templates)"""
        return f"/products/{self.product.id}/?option={self.id}"

class User(models.Model):
    email = models.EmailField()
    phone = models.CharField(max_length=10)
    fullname = models.CharField(max_length=100)
    password = models.CharField(max_length=5000)
    wishlist = models.ManyToManyField(ProductOption, blank=True, related_name="wishlist")
    cart = models.ManyToManyField(ProductOption, blank=True, related_name="cart")
    service_wishlist = models.ManyToManyField(
        'ServiceOption',
        blank=True,
        related_name="service_wishlist",
        through='ServiceWishlistItem'  # For additional metadata
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # address fields
    name = models.CharField(max_length=100, blank=True)
    address = models.TextField(max_length=1000, blank=True)
    pincode = models.IntegerField(blank=True, null=True)
    contact_no = models.CharField(max_length=10, blank=True)
    district = models.CharField(max_length=500, blank=True)
    state = models.CharField(max_length=500, blank=True)

    def __str__(self):
        return self.email


class ServiceWishlistItem(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='service_wishlist_items')
    service_option = models.ForeignKey('ServiceOption', on_delete=models.CASCADE)
    service = models.ForeignKey('Service', on_delete=models.CASCADE)  # For quick access
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'service_option']
        ordering = ['-added_at']

    def __str__(self):
        return f"{self.user.email} - {self.service.title}"


class Token(models.Model):
    token = models.CharField(max_length=5000)
    fcmtoken = models.CharField(max_length=5000)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tokens_set")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.email


class PasswordResetToken(models.Model):
    token = models.CharField(max_length=5000)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_tokens_set")
    validity = models.DateTimeField()

    def __str__(self):
        return self.user.email


class ProductImage(models.Model):
    position = models.IntegerField(default=0)
    image = models.ImageField(upload_to='product/')
    product_option = models.ForeignKey(ProductOption, on_delete=models.CASCADE, related_name='images_set')


class PageItem(models.Model):
    position = models.IntegerField(default=0)
    image = models.ImageField(upload_to='product/', blank=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='pageitems_set')
    choices = [
        (1, 'BANNER'),
        (2, 'SWIPER'),
        (3, 'GRID'),
    ]
    viewtype = models.IntegerField(choices=choices)
    title = models.CharField(max_length=50, blank=True)
    product_options = models.ManyToManyField(ProductOption, blank=True)

    def __str__(self):
        return self.category.name


class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seen = models.BooleanField(default=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="orders_set")
    tx_amount = models.IntegerField(default=0)
    payment_mode = models.CharField(max_length=100, null=True)
    address = models.TextField(max_length=5000)
    tx_id = models.CharField(max_length=1000, null=True)
    tx_choices = [
        ('INITIATED', 'INITIATED'),
        ('PENDING', 'PENDING'),
        ('INCOMPLETE', 'INCOMPLETE'),
        ('FAILED', 'FAILED'),
        ('FLAGGED', 'FLAGGED'),
        ('USER_DROPPED', 'USER_DROPPED'),
        ('SUCCESS', 'SUCCESS'),
        ('CANCELLED', 'CANCELLED'),
        ('VOID', 'VOID'),
    ]
    tx_status = models.CharField(choices=tx_choices, max_length=100, null=True)
    tx_time = models.CharField(max_length=500, null=True)
    tx_msg = models.CharField(max_length=500, null=True)

    from_cart = models.BooleanField(default=True)
    expected_delivery = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Expected delivery date in format 'DD MMM YYYY'"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    #todo temp.
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6,
        null=True, blank=True,
        help_text="Customer location latitude"
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6,
        null=True, blank=True,
        help_text="Customer location longitude"
    )

    VENDOR_STATUS_CHOICES = [
        ('PENDING', 'Pending Vendor Approval'),
        ('ACCEPTED', 'Accepted by Vendor'),
        ('REJECTED', 'Rejected by Vendor'),
        ('PROCESSING', 'Processing'),
        ('READY', 'Ready for Delivery'),
    ]

    vendor_status = models.CharField(
        max_length=20,
        choices=VENDOR_STATUS_CHOICES,
        default='PENDING',
        help_text="Vendor's order status"
    )
    vendor_notes = models.TextField(blank=True, help_text="Vendor's notes/reason")
    vendor_accepted_at = models.DateTimeField(null=True, blank=True)
    vendor_rejected_at = models.DateTimeField(null=True, blank=True)
    assigned_vendor = models.ForeignKey(
        Vendor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_orders'
    )

    class Meta:
        indexes = [
            models.Index(fields=['vendor_status', 'created_at']),
            models.Index(fields=['assigned_vendor', 'vendor_status']),
        ]


class OrderedProduct(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="orders_set")
    product_option = models.ForeignKey(ProductOption, on_delete=models.CASCADE, related_name="order_options_set")
    product_price = models.IntegerField(default=0)
    tx_price = models.IntegerField(default=0)
    delivery_price = models.IntegerField(default=0)
    quantity = models.IntegerField(default=1)

    # Rental fields
    rental_type = models.CharField(max_length=10, choices=[('rent', 'Rent'), ('buy', 'Buy')], default='buy')
    rental_duration = models.CharField(max_length=20, blank=True, null=True)
    rental_start_date = models.DateField(null=True, blank=True)
    rental_end_date = models.DateField(null=True, blank=True)

    # Rating and Review fields
    rating = models.IntegerField(default=0)
    review_text = models.TextField(blank=True, default='', help_text="Customer review text")  # ✅ NEW
    rated_at = models.DateTimeField(null=True, blank=True, help_text="When review was submitted")  # ✅ NEW

    choices = [
        ('ORDERED', 'ORDERED'),
        ('OUT_FOR_DELIVERY', 'OUT_FOR_DELIVERY'),
        ('DELIVERED', 'DELIVERED'),
        ('CANCELLED', 'CANCELLED'),
        ('RETURNED', 'RETURNED'),
    ]
    status = models.CharField(choices=choices, default='ORDERED', max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        rental_info = f" [{self.rental_type.upper()}]" if self.rental_type else ""
        return f"{self.product_option}{rental_info}"

class VendorProduct(models.Model):
    """Link vendors to products they manage"""
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='vendor_products')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='product_vendors')
    can_edit = models.BooleanField(default=True)
    can_view_orders = models.BooleanField(default=True)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['vendor', 'product']
        verbose_name = "Vendor Product Assignment"
        verbose_name_plural = "Vendor Product Assignments"

    def __str__(self):
        return f"{self.vendor.name} - {self.product.title}"

class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications_set")
    title = models.CharField(max_length=225)
    body = models.TextField(max_length=1000)
    seen = models.BooleanField(default=False)
    image = models.ImageField(upload_to="notifications/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class UserDevice(models.Model):
    """Stores FCM token per user for push notifications (e.g. order accepted)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="devices")
    fcm_token = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["user", "fcm_token"]]
        verbose_name = "User device (FCM)"
        verbose_name_plural = "User devices (FCM)"

    def __str__(self):
        return f"{self.user.email} - {self.fcm_token[:20]}..."


class AdminNotificationLog(models.Model):
    """Log of push notifications sent from admin panel."""
    TARGET_ALL = 'all'
    TARGET_SELECTED = 'selected'
    TARGET_CHOICES = [
        (TARGET_ALL, 'All users'),
        (TARGET_SELECTED, 'Selected users'),
    ]
    title = models.CharField(max_length=255)
    body = models.TextField()
    target_type = models.CharField(max_length=20, choices=TARGET_CHOICES)
    target_count = models.PositiveIntegerField(default=0, help_text='Number of devices/users targeted')
    data = models.JSONField(blank=True, null=True, help_text='Optional data payload for deep linking (e.g. {"screen": "orders"})')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Send push notification'
        verbose_name_plural = 'Send push notifications'

    def __str__(self):
        return f"{self.title} ({self.target_type}) @ {self.created_at}"


class ContactInfo(models.Model):
    phone_number = models.CharField(max_length=15)

    def __str__(self):
        return self.phone_number



class InformMe(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='inform_me_requests'
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='inform_me_requests'
    )
    product_option = models.ForeignKey(
        ProductOption,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='inform_me_requests'
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)
    offer_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Inform Me Request"
        verbose_name_plural = "Inform Me Requests"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email} Ã¢â€ â€™ {self.product.title}"


class AppVersion(models.Model):
    PLATFORM_CHOICES = [
        ('android', 'Android'),
        ('ios', 'iOS'),
    ]

    platform = models.CharField(
        max_length=10,
        choices=PLATFORM_CHOICES,
        help_text="Select platform (Android or iOS)"
    )
    version_name = models.CharField(
        max_length=20,
        help_text="Version number (e.g., 2.0.0, 3.1.5)"
    )
    version_code = models.IntegerField(
        help_text="Build number (e.g., 1, 2, 3...)"
    )
    min_supported_version = models.CharField(
        max_length=20,
        help_text="Minimum version required (users below this will be forced to update)"
    )
    min_supported_code = models.IntegerField(
        help_text="Minimum build number required"
    )
    is_force_update = models.BooleanField(
        default=False,
        help_text="Force all users to update (regardless of minimum version)"
    )
    update_message = models.TextField(
        blank=True,
        help_text="Custom message shown to users",
        default="A new version is available with exciting new features!"
    )
    release_notes = models.TextField(
        blank=True,
        help_text="What's new in this version (shown in update dialog)"
    )
    store_url = models.URLField(
        help_text="App store download URL"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Only active versions will be used for update checks"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['platform', 'version_name']
        ordering = ['-created_at']
        verbose_name = "App Version"
        verbose_name_plural = "App Versions"

    def __str__(self):
        return f"{self.get_platform_display()} v{self.version_name} ({'Active' if self.is_active else 'Inactive'})"

    def save(self, *args, **kwargs):
        # Set default store URLs if not provided
        if not self.store_url:
            if self.platform == 'android':
                self.store_url = 'https://play.google.com/store/apps/details?id=com.chaitanya.clickwell'
            elif self.platform == 'ios':
                self.store_url = 'https://apps.apple.com/in/app/clickwell-grocery-delivery/id6741314210'

        # Set default release notes if not provided
        if not self.release_notes:
            self.release_notes = f"Ã°Å¸Å½â€° What's New in ClickWell v{self.version_name}:\n\nÃ¢â‚¬Â¢ Bug fixes and improvements\nÃ¢â‚¬Â¢ Enhanced performance\nÃ¢â‚¬Â¢ Better user experience"

        super().save(*args, **kwargs)
###todo services


class ServiceCategory(models.Model):
    name = models.CharField(max_length=100)
    position = models.IntegerField(default=0)
    image = models.ImageField(upload_to='service_categories/', null=True, blank=True)
    icon = models.CharField(max_length=50, blank=True, help_text="Icon name from your icon library")
    color = models.CharField(max_length=7, default='#667EEA', help_text="Hex color code")

    class Meta:
        verbose_name = "Service Category"
        verbose_name_plural = "Service Categories"
        ordering = ['position']

    def __str__(self):
        return self.name


class ServiceSubCategory(models.Model):
    """
    Sub-category under a Service Category (e.g. under Decoration: Bridal Entry, Haldi, Mehendi, Sangeet).
    Admin creates these independently; services are assigned to a sub-category.
    """
    category = models.ForeignKey(
        ServiceCategory,
        on_delete=models.CASCADE,
        related_name='subcategories',
    )
    name = models.CharField(max_length=150)
    position = models.IntegerField(default=0)
    image = models.ImageField(upload_to='service_subcategories/', null=True, blank=True)

    class Meta:
        verbose_name = "Service Sub-category"
        verbose_name_plural = "Service Sub-categories"
        ordering = ['category', 'position']
        unique_together = [['category', 'name']]

    def __str__(self):
        return f"{self.name} ({self.category.name})"


# models.py - Update Service model

class Service(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(ServiceCategory, on_delete=models.CASCADE, related_name='services_set', null=True)
    subcategory = models.ForeignKey(
        'ServiceSubCategory',
        on_delete=models.SET_NULL,
        related_name='services_set',
        null=True,
        blank=True,
        help_text="Optional: assign to a sub-category (e.g. Bridal Entry, Haldi). Category can be set from sub-category.",
    )
    title = models.CharField(max_length=500)
    description = models.TextField(max_length=100000)
    base_price = models.IntegerField(default=0, help_text="Starting price")
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=0.0)
    total_reviews = models.IntegerField(default=0)
    experience_years = models.IntegerField(default=0, help_text="Years of experience")
    availability = models.BooleanField(default=True)
    location = models.CharField(max_length=500, blank=True)

    # Service provider details
    provider_name = models.CharField(max_length=200, default='')
    provider_phone = models.CharField(max_length=15, default='')
    provider_email = models.EmailField(blank=True)

    # âœ… NEW: Languages spoken
    languages = models.CharField(
        max_length=200,
        default='Hindi, English',
        help_text="Comma-separated list of languages (e.g., Hindi, English, Marathi)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Service"
        verbose_name_plural = "Services"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} - {self.provider_name}"

    def get_languages_list(self):
        """Return languages as a list"""
        return [lang.strip() for lang in self.languages.split(',') if lang.strip()]

    def save(self, *args, **kwargs):
        if self.subcategory_id and not self.category_id:
            self.category_id = self.subcategory.category_id
        elif self.subcategory_id:
            self.category_id = self.subcategory.category_id
        super().save(*args, **kwargs)


class ServiceOption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='options_set')
    option_name = models.CharField(max_length=200, help_text="e.g., Basic Package, Premium Package")
    description = models.TextField(max_length=1000, blank=True)
    price = models.IntegerField(default=0)
    duration = models.CharField(max_length=100, blank=True, help_text="e.g., 2 hours, 1 day")
    available = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Service Option"
        verbose_name_plural = "Service Options"

    def __str__(self):
        return f"{self.option_name} - {self.service.title}"


class ServiceImage(models.Model):
    position = models.IntegerField(default=0)
    image = models.ImageField(upload_to='services/')
    service_option = models.ForeignKey(ServiceOption, on_delete=models.CASCADE, related_name='images_set')

    class Meta:
        verbose_name = "Service Image"
        verbose_name_plural = "Service Images"
        ordering = ['position']

    def __str__(self):
        return f"Image {self.position} - {self.service_option.service.title}"


class ServicePageItem(models.Model):
    position = models.IntegerField(default=0)
    image = models.ImageField(upload_to='service_page_items/', blank=True)
    category = models.ForeignKey(ServiceCategory, on_delete=models.CASCADE, related_name='page_items_set')
    choices = [
        (1, 'BANNER'),
        (2, 'SWIPER'),
        (3, 'GRID'),
    ]
    viewtype = models.IntegerField(choices=choices)
    title = models.CharField(max_length=100, blank=True)
    service_options = models.ManyToManyField(ServiceOption, blank=True)

    class Meta:
        verbose_name = "Service Page Item"
        verbose_name_plural = "Service Page Items"
        ordering = ['category__position', 'position']

    def __str__(self):
        return f"{self.title} - {self.category.name}"


class ServiceBooking(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('CONFIRMED', 'Confirmed'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]

    PAYMENT_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PAID', 'Paid'),
        ('FAILED', 'Failed'),
        ('REFUNDED', 'Refunded'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='service_bookings')
    service_option = models.ForeignKey('ServiceOption', on_delete=models.CASCADE)
    booking_date = models.DateField()
    booking_time = models.TimeField()
    duration = models.CharField(max_length=50, default='1 hour')
    customer_name = models.CharField(max_length=255)
    customer_phone = models.CharField(max_length=20)
    customer_address = models.TextField()
    total_amount = models.IntegerField()
    notes = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='PENDING')
    rating = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    review_text = models.TextField(blank=True, default='')
    rated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.customer_name} - {self.service_option.service.title} on {self.booking_date}"


class ArtistAvailability(models.Model):
    """
    Per-artist (per-service) calendar: block, book, or mark available.
    One record per artist per date. Blocking Radha does NOT affect other artists.
    """
    STATUS_BLOCKED = 'blocked'
    STATUS_BOOKED = 'booked'
    STATUS_AVAILABLE = 'available'
    STATUS_CHOICES = [
        (STATUS_BLOCKED, 'Blocked'),
        (STATUS_BOOKED, 'Booked'),
        (STATUS_AVAILABLE, 'Available'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    artist = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name='artist_availability',
        help_text='Service/Artist (e.g. Radha Makeup Artist)',
    )
    service_type = models.CharField(
        max_length=50,
        default='makeup',
        help_text='e.g. makeup, mehndi',
    )
    date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_AVAILABLE)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Artist availability'
        verbose_name_plural = 'Artist availability'
        ordering = ['artist', 'date']
        unique_together = [['artist', 'date']]

    def __str__(self):
        return f"{self.artist.title} - {self.date} ({self.status})"


class ServiceableLocation(models.Model):
    """
    Pincodes where services are available
    """
    pincode = models.CharField(max_length=6, unique=True, help_text="6-digit pincode")
    area_name = models.CharField(max_length=200, help_text="Area/locality name")
    city = models.CharField(max_length=100, default="Agar Malwa")
    state = models.CharField(max_length=100, default="Madhya Pradesh")
    is_active = models.BooleanField(default=True, help_text="Enable/disable service in this area")

    # Geolocation coordinates (optional, for future use)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # Service availability
    rent_available = models.BooleanField(default=True, help_text="Enable rent services")
    service_available = models.BooleanField(default=True, help_text="Enable other services")

    # Delivery settings
    delivery_charge = models.IntegerField(default=0, help_text="Area-specific delivery charge")
    delivery_time = models.CharField(max_length=50, default="1-2 days", help_text="Expected delivery time")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Serviceable Location"
        verbose_name_plural = "Serviceable Locations"
        ordering = ['pincode']

    def __str__(self):
        return f"{self.pincode} - {self.area_name}"


class CategoryAvailability(models.Model):
    """
    Control which categories are available in which pincodes
    """
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='location_availability')
    location = models.ForeignKey(ServiceableLocation, on_delete=models.CASCADE, related_name='available_categories')
    is_available = models.BooleanField(default=True)
    priority = models.IntegerField(default=0, help_text="Display priority in this location")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Category Availability"
        verbose_name_plural = "Category Availabilities"
        unique_together = ['category', 'location']
        ordering = ['-priority', 'category__position']

    def __str__(self):
        return f"{self.category.name} in {self.location.pincode}"


class PageItemAvailability(models.Model):
    """
    Control which page items are shown in which pincodes
    """
    page_item = models.ForeignKey(PageItem, on_delete=models.CASCADE, related_name='location_availability')
    location = models.ForeignKey(ServiceableLocation, on_delete=models.CASCADE, related_name='available_page_items')
    is_available = models.BooleanField(default=True)
    priority = models.IntegerField(default=0, help_text="Display priority in this location")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Page Item Availability"
        verbose_name_plural = "Page Item Availabilities"
        unique_together = ['page_item', 'location']
        ordering = ['-priority', 'page_item__position']

    def __str__(self):
        return f"{self.page_item.title} in {self.location.pincode}"


class ServiceCategoryAvailability(models.Model):
    """
    Control which service categories are available in which pincodes
    """
    service_category = models.ForeignKey(ServiceCategory, on_delete=models.CASCADE,
                                         related_name='location_availability')
    location = models.ForeignKey(ServiceableLocation, on_delete=models.CASCADE,
                                 related_name='available_service_categories')
    is_available = models.BooleanField(default=True)
    priority = models.IntegerField(default=0, help_text="Display priority in this location")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Service Category Availability"
        verbose_name_plural = "Service Category Availabilities"
        unique_together = ['service_category', 'location']
        ordering = ['-priority', 'service_category__position']

    def __str__(self):
        return f"{self.service_category.name} in {self.location.pincode}"


# models.py - Add this new model

# models.py - Update HomePageItem model

class HomePageItem(models.Model):
    """
    Unified home page items for both Rents and Services
    Can be filtered by location
    âœ… ALL FIELDS OPTIONAL FOR FLEXIBILITY
    """
    ITEM_TYPE_CHOICES = [
        ('rent', 'Rent Products'),
        ('service', 'Services'),
    ]

    VIEW_TYPE_CHOICES = [
        (1, 'BANNER'),
        (2, 'SWIPER'),
        (3, 'GRID'),
    ]

    id = models.AutoField(primary_key=True)

    title = models.CharField(
        max_length=200,
        blank=True,  # âœ… Made optional
        default='',  # âœ… Added default
        help_text="Display title for the section"
    )

    subtitle = models.CharField(
        max_length=200,
        blank=True,
        default='',
        help_text="Optional subtitle (e.g. Handpicked styles for you)"
    )

    item_type = models.CharField(
        max_length=10,
        choices=ITEM_TYPE_CHOICES,
        default='rent',  # âœ… Added default
        help_text="Whether this shows products or services"
    )

    position = models.PositiveIntegerField(
        default=0,
        help_text="Display order (lower numbers appear first)"
    )

    viewtype = models.IntegerField(
        choices=VIEW_TYPE_CHOICES,
        default=3,
        help_text="How items are displayed"
    )

    image = models.ImageField(
        upload_to='home_page_items/',
        blank=True,
        null=True,
        help_text="Optional banner image"
    )

    # Category association - âœ… MADE OPTIONAL
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name='home_page_items',
        null=True,  # âœ… Made optional
        blank=True,  # âœ… Made optional
        help_text="Category for rent products"
    )

    service_category = models.ForeignKey(
        ServiceCategory,
        on_delete=models.CASCADE,
        related_name='home_page_items',
        null=True,  # âœ… Made optional
        blank=True,  # âœ… Made optional
        help_text="Category for services"
    )

    # Items to display - âœ… ALREADY OPTIONAL (ManyToMany with blank=True)
    product_options = models.ManyToManyField(
        ProductOption,
        blank=True,
        related_name='home_page_items',
        help_text="Products to show (for rent type)"
    )

    service_options = models.ManyToManyField(
        ServiceOption,
        blank=True,
        related_name='home_page_items',
        help_text="Services to show (for service type)"
    )

    # Availability settings
    is_active = models.BooleanField(
        default=True,
        help_text="Show/hide this item"
    )

    show_in_all_locations = models.BooleanField(
        default=True,
        help_text="Show in all serviceable locations"
    )

    specific_locations = models.ManyToManyField(
        ServiceableLocation,
        blank=True,
        related_name='home_page_items',
        help_text="Show only in these locations (if not showing in all)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Home Page Item"
        verbose_name_plural = "Home Page Items"
        ordering = ['item_type', 'position', 'title']
        indexes = [
            models.Index(fields=['item_type', 'position', 'is_active']),
        ]

    def __str__(self):
        type_display = "ðŸ  Rent" if self.item_type == 'rent' else "ðŸ› ï¸ Service"
        title = self.title or "Untitled"
        return f"{type_display} - {title} (Position: {self.position})"

    def clean(self):
        """
        âœ… RELAXED VALIDATION - Only warn, don't block
        """
        # No validation errors - just allow everything
        pass

    def get_items_count(self):
        """Get count of associated items"""
        if self.item_type == 'rent':
            return self.product_options.count()
        else:
            return self.service_options.count()

    def is_available_in_location(self, pincode):
        """Check if this item is available in a specific location"""
        if not self.is_active:
            return False

        if self.show_in_all_locations:
            return True

        return self.specific_locations.filter(pincode=pincode, is_active=True).exists()
