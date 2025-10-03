import uuid

import os
from django.db import models
import uuid
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class Otp(models.Model):
    phone = models.CharField(max_length=10)
    otp = models.IntegerField()
    validity = models.DateTimeField()
    verified = models.BooleanField(default=False)

    def __str__(self):
        return self.phone


class Category(models.Model):
    name = models.CharField(max_length=50)
    position = models.IntegerField(default=0)
    image = models.ImageField(upload_to='categories/')

    def __str__(self):
        return self.name


class Slide(models.Model):
    position = models.IntegerField(default=0)
    image = models.ImageField(upload_to='categories/')


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='products_set')
    title = models.CharField(max_length=500)
    description = models.TextField(max_length=100000)
    price = models.IntegerField(default=0)
    offer_price = models.IntegerField(default=0)
    delivery_charge = models.IntegerField(default=0)
    star_5 = models.IntegerField(default=0)
    star_4 = models.IntegerField(default=0)
    star_3 = models.IntegerField(default=0)
    star_2 = models.IntegerField(default=0)
    star_1 = models.IntegerField(default=0)
    cod = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class ProductOption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='options_set')
    option = models.CharField(max_length=50, blank=True)
    quantity = models.IntegerField(default=0)

    def __str__(self):
        return f"({self.option}) {self.product.title}"


class User(models.Model):
    email = models.EmailField()
    phone = models.CharField(max_length=10)
    fullname = models.CharField(max_length=100)
    password = models.CharField(max_length=5000)
    wishlist = models.ManyToManyField(ProductOption, blank=True, related_name="wishlist")
    cart = models.ManyToManyField(ProductOption, blank=True, related_name="cart")
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



class OrderedProduct(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="orders_set")
    product_option = models.ForeignKey(ProductOption, on_delete=models.CASCADE, related_name="order_options_set")
    product_price = models.IntegerField(default=0)
    tx_price = models.IntegerField(default=0)
    delivery_price = models.IntegerField(default=0)
    quantity = models.IntegerField(default=1)
    choices = [
        ('ORDERED', 'ORDERED'),
        ('OUT_FOR_DELIVERY', 'OUT_FOR_DELIVERY'),
        ('DELIVERED', 'DELIVERED'),
        ('CANCELLED', 'CANCELLED'),
    ]
    rating = models.IntegerField(default=0)
    status = models.CharField(choices=choices, default='ORDERED', max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications_set")
    title = models.CharField(max_length=225)
    body = models.TextField(max_length=1000)
    seen = models.BooleanField(default=False)
    image = models.ImageField(upload_to="notifications/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

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
        return f"{self.user.email} → {self.product.title}"


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
            self.release_notes = f"🎉 What's New in ClickWell v{self.version_name}:\n\n• Bug fixes and improvements\n• Enhanced performance\n• Better user experience"

        super().save(*args, **kwargs)


class Coupon(models.Model):
    DISCOUNT_TYPES = [
        ('percentage', 'Percentage'),
        ('fixed', 'Fixed Amount'),
    ]

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    max_discount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    min_order_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    usage_limit = models.IntegerField(null=True, blank=True, help_text="Total usage limit")
    usage_limit_per_user = models.IntegerField(default=1, help_text="Usage limit per user")
    used_count = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.code} - {self.name}"

    def is_valid(self, user=None, cart_amount=0):
        """Check if coupon is valid for use"""
        from django.utils import timezone

        # Check if coupon is active
        if not self.is_active:
            return False, "Coupon is not active"

        # Check date validity
        now = timezone.now()
        if now < self.valid_from:
            return False, "Coupon is not yet valid"
        if now > self.valid_until:
            return False, "Coupon has expired"

        # Check minimum order amount
        if cart_amount < self.min_order_amount:
            return False, f"Minimum order amount of ₹{self.min_order_amount} required"

        # Check total usage limit
        if self.usage_limit and self.used_count >= self.usage_limit:
            return False, "Coupon usage limit reached"

        # Check per-user usage limit
        if user:
            user_usage = CouponUsage.objects.filter(coupon=self, user=user).count()
            if user_usage >= self.usage_limit_per_user:
                return False, "You have already used this coupon maximum times"

        return True, "Coupon is valid"

    def calculate_discount(self, cart_amount):
        """Calculate discount amount for given cart amount"""
        if self.discount_type == 'percentage':
            discount = (cart_amount * self.discount_value) / 100
            if self.max_discount:
                discount = min(discount, self.max_discount)
        else:  # fixed
            discount = self.discount_value

        return min(discount, cart_amount)  # Discount can't exceed cart amount

    class Meta:
        ordering = ['-created_at']


class CouponUsage(models.Model):
    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name='usages')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='coupon_usages')
    order = models.ForeignKey('Order', on_delete=models.CASCADE, null=True, blank=True)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2)
    used_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.email} used {self.coupon.code}"

    class Meta:
        unique_together = ['coupon', 'user', 'order']
        ordering = ['-used_at']


class BeautyService(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    description = models.TextField(max_length=1000, blank=True)
    category = models.CharField(max_length=50)  # e.g., 'makeup', 'mehndi', 'hair'
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    duration_minutes = models.IntegerField(default=60)
    icon = models.CharField(max_length=10, default='💄')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['category', 'name']


class Artist(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=15)
    email = models.EmailField(blank=True)
    bio = models.TextField(max_length=500, blank=True)
    profile_image = models.ImageField(upload_to='artists/', blank=True)

    # Professional details
    experience_years = models.IntegerField(default=0)
    specializations = models.ManyToManyField(BeautyService, related_name='artists')

    # Ratings and reviews
    total_bookings = models.IntegerField(default=0)
    average_rating = models.DecimalField(max_digits=3, decimal_places=2, default=0.0)
    total_reviews = models.IntegerField(default=0)

    # Availability
    is_available = models.BooleanField(default=True)
    location = models.CharField(max_length=200, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-average_rating', 'name']


class Booking(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('CONFIRMED', 'Confirmed'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
        ('RESCHEDULED', 'Rescheduled'),
    ]

    PAYMENT_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PAID', 'Paid'),
        ('FAILED', 'Failed'),
        ('REFUNDED', 'Refunded'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking_number = models.CharField(max_length=20, unique=True, blank=True)

    # Relationships
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bookings')
    service = models.ForeignKey(BeautyService, on_delete=models.CASCADE)
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name='bookings')

    # Booking details
    scheduled_date = models.DateField()
    scheduled_time = models.TimeField()
    duration_minutes = models.IntegerField()

    # Pricing
    service_price = models.DecimalField(max_digits=10, decimal_places=2)
    additional_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Status and payment
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='PENDING')
    payment_method = models.CharField(max_length=50, blank=True)
    transaction_id = models.CharField(max_length=100, blank=True)

    # Location
    service_address = models.TextField(max_length=500)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # Notes and special requests
    customer_notes = models.TextField(max_length=1000, blank=True)
    artist_notes = models.TextField(max_length=1000, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.booking_number:
            self.booking_number = f"BH{str(self.id)[:8].upper()}" if self.id else f"BH{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.booking_number} - {self.service.name}"

    @property
    def is_active(self):
        return self.status in ['PENDING', 'CONFIRMED', 'IN_PROGRESS']

    @property
    def can_cancel(self):
        from django.utils import timezone
        from datetime import datetime, timedelta

        # Can cancel if booking is at least 2 hours away
        booking_datetime = datetime.combine(self.scheduled_date, self.scheduled_time)
        return (self.status in ['PENDING', 'CONFIRMED'] and
                booking_datetime > timezone.now() + timedelta(hours=2))

    @property
    def can_reschedule(self):
        from django.utils import timezone
        from datetime import datetime, timedelta

        # Can reschedule if booking is at least 4 hours away
        booking_datetime = datetime.combine(self.scheduled_date, self.scheduled_time)
        return (self.status in ['PENDING', 'CONFIRMED'] and
                booking_datetime > timezone.now() + timedelta(hours=4))

    class Meta:
        ordering = ['-scheduled_date', '-scheduled_time']


class BookingRating(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='rating')

    # Ratings (1-5 scale)
    overall_rating = models.IntegerField(choices=[(i, i) for i in range(1, 6)])
    service_quality = models.IntegerField(choices=[(i, i) for i in range(1, 6)], null=True, blank=True)
    punctuality = models.IntegerField(choices=[(i, i) for i in range(1, 6)], null=True, blank=True)
    professionalism = models.IntegerField(choices=[(i, i) for i in range(1, 6)], null=True, blank=True)

    # Review
    review_text = models.TextField(max_length=1000, blank=True)

    # Metadata
    is_anonymous = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.booking.booking_number} - {self.overall_rating} stars"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        # Update artist's average rating
        artist = self.booking.artist
        ratings = BookingRating.objects.filter(booking__artist=artist)
        if ratings.exists():
            artist.average_rating = ratings.aggregate(
                avg_rating=models.Avg('overall_rating')
            )['avg_rating']
            artist.total_reviews = ratings.count()
            artist.save()

    class Meta:
        ordering = ['-created_at']


class BookingHistory(models.Model):
    """Track status changes for bookings"""
    ACTION_CHOICES = [
        ('CREATED', 'Booking Created'),
        ('CONFIRMED', 'Booking Confirmed'),
        ('CANCELLED', 'Booking Cancelled'),
        ('RESCHEDULED', 'Booking Rescheduled'),
        ('COMPLETED', 'Service Completed'),
        ('PAYMENT_RECEIVED', 'Payment Received'),
        ('REFUND_PROCESSED', 'Refund Processed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='history')
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    description = models.CharField(max_length=200, blank=True)
    performed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.booking.booking_number} - {self.action}"

    class Meta:
        ordering = ['-created_at']