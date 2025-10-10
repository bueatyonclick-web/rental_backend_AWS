import uuid

import os

from django.contrib.auth.hashers import make_password
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
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='products_set', null=True, blank=True)  # ADD THIS LINE
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


class Service(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(ServiceCategory, on_delete=models.CASCADE, related_name='services_set',null=True)
    title = models.CharField(max_length=500)
    description = models.TextField(max_length=100000)
    base_price = models.IntegerField(default=0, help_text="Starting price")
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=0.0)
    total_reviews = models.IntegerField(default=0)
    experience_years = models.IntegerField(default=0, help_text="Years of experience")
    availability = models.BooleanField(default=True)
    location = models.CharField(max_length=500, blank=True)

    # Service provider details
    provider_name = models.CharField(max_length=200,default='')
    provider_phone = models.CharField(max_length=15,default='')
    provider_email = models.EmailField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Service"
        verbose_name_plural = "Services"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} - {self.provider_name}"


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