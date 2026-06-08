from datetime import datetime, timezone, timedelta
from urllib import request

from django.core.validators import MinValueValidator, MaxValueValidator
from rest_framework.fields import SerializerMethodField
from rest_framework.serializers import ModelSerializer
from rest_framework import serializers

from django.db.models import Sum, Count, Avg, Q


from backend import models
from backend.models import User, Category, Slide, Product, ProductOption, ProductImage, PageItem, OrderedProduct, \
    Notification, ContactInfo, InformMe, AppVersion, ServiceImage, ServiceOption, ServiceCategory, ServiceSubCategory, Service, \
    ServicePageItem, ServiceBooking, ProductBooking, Order, ServiceableLocation, HomePageItem, ServiceWishlistItem


class UserSerializer(ModelSerializer):
    notifications = SerializerMethodField()
    class Meta:
        model = User
        fields = ['email','notifications', 'phone', 'fullname', 'wishlist', 'cart', 'name', 'address', 'contact_no', 'pincode', 'state',
                  'district']

    def get_notifications(self,obj):
        list = obj.notifications_set.filter(seen=False)
        return len(list)

    def to_representation(self, instance):
        if instance is None:
            return None
        return super().to_representation(instance)


class AddressSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = ['name', 'address', 'contact_no', 'pincode', 'state', 'district']


class CategorySerializer(ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'position', 'image', 'gender']

    def get_image(self, obj):
        request = self.context.get('request')
        if obj.image:
            image_url = obj.image.url
            return request.build_absolute_uri(image_url) if request else image_url
        return None


class SlideSerializer(ModelSerializer):
    class Meta:
        model = Slide
        fields = ['position', 'image']


# Enhanced ProductSerializer with better image handling


class ProductSerializer(ModelSerializer):
    options = serializers.SerializerMethodField()
    category_name = serializers.SerializerMethodField()
    requires_date_selection = serializers.BooleanField()
    max_bookings_per_date = serializers.IntegerField()
    booked_dates = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'title', 'description', 'price', 'offer_price', 'delivery_charge',
            'cod', 'position', 'star_5', 'star_4', 'star_3', 'star_2', 'star_1',
            'options', 'category_name', 'requires_date_selection',
            'max_bookings_per_date', 'booked_dates', 'created_at', 'updated_at'
        ]

    def get_options(self, obj):
        options = obj.options_set.all()
        return ProductOptionSerializer(options, many=True, context=self.context).data

    def get_category_name(self, obj):
        return obj.category.name if obj.category else None

    def get_booked_dates(self, obj):
        """Get all booked dates for this product for the next 60 days"""
        from django.utils import timezone
        from datetime import timedelta

        if not obj.requires_date_selection:
            return []

        today = timezone.now().date()
        end_date = today + timedelta(days=60)

        # Get all bookings for this product in the date range
        bookings = ProductBooking.objects.filter(
            product=obj,
            booking_date__gte=today,
            booking_date__lte=end_date,
            status__in=['PENDING', 'CONFIRMED']
        ).values('booking_date').annotate(
            total_booked=Sum('quantity_booked')  # Ã¢Å“â€¦ FIXED - use Sum directly
        )

        booked_dates_data = []
        for booking in bookings:
            max_per_date = obj.max_bookings_per_date if obj.max_bookings_per_date > 0 else 999999
            available = max_per_date - booking['total_booked']

            booked_dates_data.append({
                'date': booking['booking_date'].strftime('%Y-%m-%d'),
                'available_quantity': max(0, available),
                'is_fully_booked': available <= 0
            })

        return booked_dates_data


class AddToCartWithDateSerializer(serializers.Serializer):
    """Serializer for adding items to cart with date"""
    product_option_id = serializers.UUIDField()
    quantity = serializers.IntegerField(default=1, min_value=1)
    selected_date = serializers.DateField(required=False, allow_null=True)

    def validate(self, data):
        from django.utils import timezone

        # Check if selected_date is in the past
        if data.get('selected_date'):
            if data['selected_date'] < timezone.now().date():
                raise serializers.ValidationError("Selected date cannot be in the past")

        return data


class CheckDateAvailabilitySerializer(serializers.Serializer):
    """Serializer for checking date availability"""
    product_id = serializers.UUIDField()
    product_option_id = serializers.UUIDField(required=False, allow_null=True)
    date = serializers.DateField()
    quantity = serializers.IntegerField(default=1, min_value=1)


# serializers.py - Add to ProductOptionSerializer

class ProductOptionSerializer(ModelSerializer):
    images = SerializerMethodField()
    offer_price_per_day = SerializerMethodField()
    rental_pricing = SerializerMethodField()
    effective_price = SerializerMethodField()
    effective_offer_price = SerializerMethodField()
    rent_available = serializers.BooleanField(source='is_rent_available')
    buy_available = serializers.BooleanField(source='is_buy_available')

    class Meta:
        model = ProductOption
        fields = [
            'id', 'option', 'quantity', 'images',
            'rent_available',
            'buy_available',
            'rental_pricing',
            'effective_price',
            'effective_offer_price',
            'offer_price_per_day',
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)

        # ✅ Force correct boolean conversion
        data['rent_available'] = bool(instance.is_rent_available)
        data['buy_available'] = bool(instance.is_buy_available)

        return data

    def get_images(self, obj):
        images = obj.images_set.all()
        data = ProductImageSerializer(
            images,
            many=True,
            context=self.context
        ).data
        return data

    def get_rental_pricing(self, obj):
        return obj.get_rental_pricing_dict()

    def get_effective_price(self, obj):
        return obj.get_price()

    def get_effective_offer_price(self, obj):
        return obj.get_offer_price()

    def get_offer_price_per_day(self, obj):
        return obj.get_price_per_day('1_day')

class ProductImageSerializer(ModelSerializer):
    image = serializers.SerializerMethodField()  # âœ… Add this

    class Meta:
        model = ProductImage
        fields = ['position', 'image', 'product_option']

    def get_image(self, obj):
        """âœ… Build absolute URL for images"""
        if obj.image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.image.url)
            return obj.image.url
        return None


class WishlistSerializer(ModelSerializer):
    id = SerializerMethodField()
    title = SerializerMethodField()
    price = SerializerMethodField()
    offer_price = SerializerMethodField()
    image = SerializerMethodField()
    rent_for_1_day = SerializerMethodField()
    offer_price_per_day = SerializerMethodField()
    rental_label = SerializerMethodField()

    def get_id(self, obj):
        return obj.product.id

    def get_title(self, obj):
        return obj.__str__()

    def get_price(self, obj):
        return obj.product.price

    def get_offer_price(self, obj):
        return obj.product.offer_price

    def get_image(self, obj):
        return ProductImageSerializer(obj.images_set.order_by('position').first(), many=False).data.get(
            'image')

    def get_rent_for_1_day(self, obj):
        """Get 1-day rental price from ProductOption"""
        return obj.get_rental_price('1_day')

    def get_offer_price_per_day(self, obj):
        """Get effective offer price per day"""
        return obj.get_price_per_day('1_day')

    def get_rental_label(self, obj):
        """Generate rental label"""
        price = self.get_offer_price_per_day(obj)
        return f'Rent for 1 day: â‚¹{int(price)}'

    class Meta:
        model = ProductOption
        fields = ['id', 'title', 'image', 'price', 'offer_price', 'rent_for_1_day', 'offer_price_per_day', 'rental_label']

class CartSerializer(WishlistSerializer):
    cod = SerializerMethodField()
    delivery_charge = SerializerMethodField()
    id = SerializerMethodField()

    def get_cod(self, obj):
        return obj.product.cod

    def get_delivery_charge(self, obj):
        return obj.product.delivery_charge

    def get_id(self, obj):
        return obj.id

    class Meta:
        model = ProductOption
        fields = ['id', 'title', 'image', 'price', 'offer_price', 'quantity', 'cod', 'delivery_charge']


class PageItemSerializer(ModelSerializer):
    product_options = SerializerMethodField()

    class Meta:
        model = PageItem
        fields = ['id', 'position', 'image', 'category', 'title', 'viewtype', 'product_options']

    def get_product_options(self, obj):
        options = obj.product_options.all()[:8]
        data = []
        for option in options:
            price_val = option.get_price() or 0
            offer_val = option.get_offer_price() or 0
            effective_price = offer_val if (offer_val > 0 and price_val and offer_val < price_val) else price_val
            cutted_price = price_val if (offer_val > 0 and price_val and offer_val < price_val) else None
            discount_percentage = round(((price_val - effective_price) / price_val) * 100) if cutted_price and price_val > 0 else 0
            data.append({
                'id': str(option.product.id),
                'option_id': str(option.id),
                'image': ProductImageSerializer(option.images_set.order_by('position').first(), many=False).data.get(
                    'image'),
                'title': option.__str__(),
                'price': price_val,
                'offer_price': offer_val,
                'effective_price': effective_price,
                'cutted_price': cutted_price,
                'discount_percentage': discount_percentage,
                'option_price': option.option_price if option.option_price > 0 else None,
                'buy_price': option.get_buy_price(),
                'rental_price_per_day': option.get_rental_price('1_day'),
            })

        return data


class OrderItemSerializer(ModelSerializer):
    title = SerializerMethodField()
    image = SerializerMethodField()
    created_at = SerializerMethodField()

    class Meta:
        model = OrderedProduct
        fields = ['id', 'title', 'image', 'created_at', 'quantity', 'status', 'rating']

    def get_title(self, obj):
        return obj.product_option.__str__()

    def get_image(self, obj):
        return ProductImageSerializer(obj.product_option.images_set.order_by('position').first(), many=False).data.get(
            'image')

    def get_created_at(self, obj):
        return obj.created_at.strftime("%d %b %Y %H:%M %p")


class OrderDetailsSerializer(OrderItemSerializer):
    payment_mode = SerializerMethodField()
    address = SerializerMethodField()
    tx_id = SerializerMethodField()
    tx_status = SerializerMethodField()
    user_phone = SerializerMethodField()

    #temp. code
    latitude = serializers.DecimalField(
        max_digits=9, decimal_places=6,
        required=False, allow_null=True
    )
    longitude = serializers.DecimalField(
        max_digits=9, decimal_places=6,
        required=False, allow_null=True
    )

    class Meta:
        model = OrderedProduct
        fields = ['id', 'title', 'image', 'created_at', 'quantity', 'status', 'rating'
                  ,'product_price','tx_price','delivery_price','payment_mode','address','tx_id','tx_status','latitude','longitude','user_phone']

    def get_payment_mode(self,obj):
        return obj.order.payment_mode

    def get_address(self,obj):
        return obj.order.address

    def get_tx_id(self,obj):
        return obj.order.tx_id

    def get_tx_status(self,obj):
        return obj.order.tx_status

    def get_user_phone(self, obj):
        return obj.order.user.phone


class NotificationSerializer(ModelSerializer):
    created_at = SerializerMethodField()
    class Meta:
        model = Notification
        fields = ['id','title','body','image','seen','created_at']


    def get_created_at(self, obj):
        return obj.created_at.strftime("%d %b %Y %H:%M %p")

class PrivacyPolicySerializer(serializers.Serializer):
    content = serializers.CharField(trim_whitespace=False)

class ContactUsSerializer(serializers.Serializer):
    content = serializers.CharField(trim_whitespace=False)


# class OrderDetailSerializer(serializers.ModelSerializer):
#     user = serializers.CharField(source='user.email', read_only=True)  # Fetch email from user

#     class Meta:
#         model = Order
#         fields = [
#             'id', 'user', 'tx_amount', 'payment_mode', 'address', 'tx_id',
#             'tx_status', 'tx_time', 'tx_msg', 'from_cart', 'created_at', 'updated_at'
#         ]




class ItemOrderSerializer(serializers.ModelSerializer):
    product_option = serializers.CharField(source='product_option.option')  # Option name
    product_title = serializers.CharField(source='product_option.product.title')  # Product title
    product_image = serializers.SerializerMethodField()  # Absolute product image URL
    user_fullname = serializers.CharField(source='order.user.fullname', read_only=True)  # User full name
    user_phone = serializers.CharField(source='order.user.phone', read_only=True)  # User phone number
    payment_mode = serializers.CharField(source='order.payment_mode', read_only=True)  # Payment mode
    tx_status = serializers.CharField(source='order.tx_status', read_only=True)  # Transaction status
    tx_msg = serializers.CharField(source='order.tx_msg', read_only=True)  # Transaction message
    address = serializers.CharField(source='order.address', read_only=True)  # Order address
    related_products = serializers.SerializerMethodField()  # Add related products


    class Meta:
        model = OrderedProduct
        fields = [
            'id',
            'quantity',
            'product_option',
            'product_title',
            'product_image',
            'user_fullname',
            'user_phone',
            'payment_mode',
            'tx_status',
            'tx_msg',
            'address',
            'tx_price',
            'product_price',
            'delivery_price',
            'status',
            'created_at',
            'updated_at',
            'related_products',
        ]

    def get_product_image(self, obj):
        if obj.product_option.images_set.exists():
            request = self.context.get('request')  # Access the request object
            image_url = obj.product_option.images_set.first().image.url
            return request.build_absolute_uri(image_url) if request else f"/{image_url.lstrip('/')}"
        return None

    def get_related_products(self, obj):
        # Get all products from the same order, excluding the current one
        related_products = OrderedProduct.objects.filter(
            order=obj.order
        ).exclude(
            id=obj.id
        )

        # Return serialized data for related products
        return RelatedProductSerializer(related_products, many=True, context=self.context).data

class ContactInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactInfo
        fields = ['phone_number']


class RelatedProductSerializer(serializers.ModelSerializer):
    product_option = serializers.CharField(source='product_option.option')
    product_title = serializers.CharField(source='product_option.product.title')
    product_image = serializers.SerializerMethodField()

    class Meta:
        model = OrderedProduct
        fields = [
            'id',
            'product_option',
            'product_title',
            'product_image',
            'quantity',
            'product_price',
            'tx_price',
            'status',
        ]

    def get_product_image(self, obj):
        if obj.product_option.images_set.exists():
            request = self.context.get('request')
            image_url = obj.product_option.images_set.first().image.url
            return request.build_absolute_uri(image_url) if request else f"/{image_url.lstrip('/')}"
        return None


# Validation serializers for API requests
class ProductCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=500)
    description = serializers.CharField(max_length=100000, allow_blank=True)
    price = serializers.IntegerField(min_value=0)
    offer_price = serializers.IntegerField(min_value=0)
    delivery_charge = serializers.IntegerField(min_value=0, default=0)
    cod = serializers.BooleanField(default=False)
    category = serializers.CharField()

    def validate_category(self, value):
        try:
            category = Category.objects.get(id=value)
            return category
        except Category.DoesNotExist:
            raise serializers.ValidationError("Category not found")

    def validate(self, data):
        if data['offer_price'] > data['price']:
            raise serializers.ValidationError("Offer price cannot be greater than original price")
        return data


class ProductOptionCreateSerializer(serializers.Serializer):
    product = serializers.CharField()
    option = serializers.CharField(max_length=50)
    quantity = serializers.IntegerField(min_value=0, default=0)

    def validate_product(self, value):
        try:
            product = Product.objects.get(id=value)
            return product
        except Product.DoesNotExist:
            raise serializers.ValidationError("Product not found")


class ProductUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=500, required=False)
    description = serializers.CharField(max_length=100000, allow_blank=True, required=False)
    price = serializers.IntegerField(min_value=0, required=False)
    offer_price = serializers.IntegerField(min_value=0, required=False)
    delivery_charge = serializers.IntegerField(min_value=0, required=False)
    cod = serializers.BooleanField(required=False)
    position = serializers.IntegerField(min_value=0, required=False)

    def validate(self, data):
        # If both price and offer_price are provided, validate them
        if 'price' in data and 'offer_price' in data:
            if data['offer_price'] > data['price']:
                raise serializers.ValidationError("Offer price cannot be greater than original price")
        return data


class ProductOptionUpdateSerializer(serializers.Serializer):
    option = serializers.CharField(max_length=50, required=False)
    quantity = serializers.IntegerField(min_value=0, required=False)


class BulkStockUpdateSerializer(serializers.Serializer):
    option_id = serializers.CharField()
    quantity = serializers.IntegerField(min_value=0)

    def validate_option_id(self, value):
        try:
            option = ProductOption.objects.get(id=value)
            return option
        except ProductOption.DoesNotExist:
            raise serializers.ValidationError("Product option not found")


class ImageUploadSerializer(serializers.Serializer):
    product_option = serializers.CharField()
    position = serializers.IntegerField(min_value=0, default=0)
    image = serializers.ImageField()

    def validate_product_option(self, value):
        try:
            option = ProductOption.objects.get(id=value)
            return option
        except ProductOption.DoesNotExist:
            raise serializers.ValidationError("Product option not found")

    def validate_image(self, value):
        # Validate file size (5MB max)
        if value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Image file too large (max 5MB)")

class InformMeSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField(read_only=True)
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    product_option = serializers.PrimaryKeyRelatedField(
        queryset=ProductOption.objects.all(),
        allow_null=True,
        required=False
    )

    class Meta:
        model = InformMe
        fields = [
            'id', 'user', 'product', 'product_option',
            'price', 'offer_price', 'created_at'
        ]
        read_only_fields = ['id', 'user', 'price', 'created_at']

class VersionCheckRequestSerializer(serializers.Serializer):
    platform = serializers.ChoiceField(choices=['android', 'ios'])
    current_version = serializers.CharField(max_length=20)
    current_build = serializers.IntegerField()
    device_id = serializers.CharField(max_length=255, required=False, allow_blank=True)

class VersionCheckResponseSerializer(serializers.Serializer):
    has_update = serializers.BooleanField()
    is_force_update = serializers.BooleanField()
    current_version = serializers.CharField()
    latest_version = serializers.CharField()
    latest_build = serializers.IntegerField()
    update_message = serializers.CharField()
    release_notes = serializers.CharField()
    store_url = serializers.URLField()
    min_supported_version = serializers.CharField()

class AppVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppVersion
        fields = '__all__'


# Add these to your serializers.py

# Add these to your existing serializers.py
# Add these to your existing serializers.py
class ServiceCategorySerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    service_count = serializers.SerializerMethodField()
    subcategories = serializers.SerializerMethodField()

    class Meta:
        model = ServiceCategory
        fields = ['id', 'name', 'position', 'image', 'icon', 'color', 'service_count', 'subcategories']

    def get_image(self, obj):
        request = self.context.get('request')
        if obj.image:
            image_url = obj.image.url
            return request.build_absolute_uri(image_url) if request else image_url
        return None

    def get_service_count(self, obj):
        """Count services in this category (direct or via subcategories)"""
        return Service.objects.filter(availability=True).filter(
            Q(category=obj) | Q(subcategory__category=obj)
        ).count()

    def get_subcategories(self, obj):
        """
        Return ALL subcategories for this category. No location/availability filter.
        When a category is available in a location, all its subcategories are shown.
        """
        subcategories = ServiceSubCategory.objects.filter(category=obj).order_by('position')
        return ServiceSubCategorySerializer(
            subcategories,
            many=True,
            context=self.context
        ).data


class ServiceSubCategorySerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    service_count = serializers.SerializerMethodField()

    class Meta:
        model = ServiceSubCategory
        fields = ['id', 'name', 'position', 'image', 'category', 'service_count']

    def get_image(self, obj):
        request = self.context.get('request')
        if obj.image:
            image_url = obj.image.url
            return request.build_absolute_uri(image_url) if request else image_url
        return None

    def get_service_count(self, obj):
        return Service.objects.filter(subcategory=obj, availability=True).count()


class ServiceImageSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = ServiceImage
        fields = ['position', 'image', 'service_option']

    def get_image(self, obj):
        request = self.context.get('request')
        if obj.image:
            image_url = obj.image.url
            return request.build_absolute_uri(image_url) if request else image_url
        return None


class ServiceOptionSerializer(serializers.ModelSerializer):
    images = SerializerMethodField()

    class Meta:
        model = ServiceOption
        fields = ['id', 'option_name', 'description', 'price', 'duration', 'available', 'images']

    def get_images(self, obj):
        images = obj.images_set.all()
        return ServiceImageSerializer(images, many=True, context=self.context).data


# serializers.py - Update ServiceSerializer

class ServiceSerializer(serializers.ModelSerializer):
    options = SerializerMethodField()
    category_name = SerializerMethodField()
    portfolio_images = SerializerMethodField()
    languages = SerializerMethodField()
    user_reviews = SerializerMethodField()  # âœ… NEW

    class Meta:
        model = Service
        fields = [
            'id', 'title', 'description', 'base_price', 'rating', 'total_reviews',
            'experience_years', 'availability', 'location', 'provider_name',
            'provider_phone', 'provider_email', 'category_name', 'options',
            'portfolio_images', 'languages', 'user_reviews',  # âœ… NEW
            'created_at', 'updated_at'
        ]

    def get_options(self, obj):
        options = obj.options_set.all()
        return ServiceOptionSerializer(options, many=True, context=self.context).data

    def get_category_name(self, obj):
        return obj.category.name if obj.category else None

    def get_portfolio_images(self, obj):
        """Collect all images from all service options to display in portfolio"""
        portfolio = []
        options = obj.options_set.all()

        for option in options:
            images = option.images_set.all().order_by('position')
            for image in images:
                request = self.context.get('request')
                image_url = None

                if image.image:
                    if request:
                        image_url = request.build_absolute_uri(image.image.url)
                    else:
                        image_url = image.image.url

                portfolio.append({
                    'id': image.id,
                    'url': image_url,
                    'position': image.position,
                    'service_option_name': option.option_name,
                    'service_option_id': str(option.id)
                })

        portfolio.sort(key=lambda x: x['position'])
        return portfolio

    def get_languages(self, obj):
        """Return languages spoken by the service provider"""
        return ['Hindi', 'English']

    # âœ… NEW: Get user reviews
    def get_user_reviews(self, obj):
        """
        Get recent user reviews for this service
        Returns top 10 most recent reviews
        """
        from backend.models import ServiceBooking

        reviews = ServiceBooking.objects.filter(
            service_option__service=obj,
            rating__isnull=False
        ).select_related('user').order_by('-rated_at')[:10]

        reviews_data = []
        for review in reviews:
            reviews_data.append({
                'id': str(review.id),
                'user_name': review.customer_name or review.user.fullname or 'Anonymous',
                'rating': review.rating,
                'review_text': review.review_text or '',
                'booking_date': review.booking_date.strftime('%B %d, %Y'),
                'rated_at': review.rated_at.isoformat() if review.rated_at else None,
                'service_option': review.service_option.option_name,
            })

        return reviews_data
class ServicePageItemSerializer(serializers.ModelSerializer):
    service_options = SerializerMethodField()
    category_name = SerializerMethodField()

    class Meta:
        model = ServicePageItem
        fields = ['id', 'position', 'image', 'category', 'category_name', 'title', 'viewtype', 'service_options']

    def get_service_options(self, obj):
        options = obj.service_options.all()[:8]
        data = []
        for option in options:
            first_image = option.images_set.first()
            image_url = None
            if first_image:
                request = self.context.get('request')
                image_url = request.build_absolute_uri(first_image.image.url) if request else first_image.image.url

            data.append({
                'id': str(option.service.id),
                'option_id': str(option.id),
                'image': image_url,
                'title': f"{option.option_name} - {option.service.title}",
                'price': option.price,
                'duration': option.duration,
                'provider_name': option.service.provider_name,
                'rating': float(option.service.rating),
                'available': option.available,
            })
        return data

    def get_category_name(self, obj):
        return obj.category.name if obj.category else None



# In serializers.py - Update ServiceBookingSerializer

class ServiceBookingSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source='service_option.service.title', read_only=True)
    service_id = serializers.CharField(source='service_option.service.id', read_only=True)  # âœ… NEW
    option_name = serializers.CharField(source='service_option.option_name', read_only=True)
    service_option_id = serializers.CharField(source='service_option.id', read_only=True)  # âœ… NEW
    provider_name = serializers.CharField(source='service_option.service.provider_name', read_only=True)
    service_image = serializers.SerializerMethodField()
    option_price = serializers.IntegerField(source='service_option.price', read_only=True)  # âœ… NEW
    option_duration = serializers.CharField(source='service_option.duration', read_only=True)  # âœ… NEW

    class Meta:
        model = ServiceBooking
        fields = [
            'id',
            'service_id',  # âœ… NEW
            'service_name',
            'service_option_id',  # âœ… NEW - This is critical
            'option_name',
            'option_price',  # âœ… NEW
            'option_duration',  # âœ… NEW
            'provider_name',
            'booking_date', 'booking_time', 'duration',
            'customer_name', 'customer_phone', 'customer_address',
            'total_amount', 'payment_status', 'status', 'notes',
            'service_image',
            'rating', 'review_text', 'rated_at',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_service_image(self, obj):
        """Get the first image of the service option"""
        first_image = obj.service_option.images_set.first()
        if first_image:
            request = self.context.get('request')
            return request.build_absolute_uri(first_image.image.url) if request else first_image.image.url
        return None
class ServiceBookingDetailSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source='service_option.service.title', read_only=True)
    option_name = serializers.CharField(source='service_option.option_name', read_only=True)
    provider_name = serializers.CharField(source='service_option.service.provider_name', read_only=True)
    provider_phone = serializers.CharField(source='service_option.service.provider_phone', read_only=True)
    service_image = serializers.SerializerMethodField()

    class Meta:
        model = ServiceBooking
        fields = [
            'id', 'service_name', 'option_name', 'provider_name', 'provider_phone',
            'booking_date', 'booking_time', 'duration',
            'customer_name', 'customer_phone', 'customer_address',
            'total_amount', 'payment_status', 'status', 'notes',
            'service_image', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_service_image(self, obj):
        first_image = obj.service_option.images_set.first()
        if first_image:
            request = self.context.get('request')
            return request.build_absolute_uri(first_image.image.url) if request else first_image.image.url
        return None

class ProductBookingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductBooking
        fields = ['id', 'booking_date', 'quantity_booked', 'status', 'created_at']


class BookedDateSerializer(serializers.Serializer):
    """Serializer for booked dates response"""
    date = serializers.DateField()
    available_quantity = serializers.IntegerField()
    is_fully_booked = serializers.BooleanField()


class VendorOrderDetailSerializer(serializers.ModelSerializer):
    """Serializer for order items"""
    product_title = serializers.CharField(source='product_option.product.title', read_only=True)
    product_option_name = serializers.CharField(source='product_option.option', read_only=True)
    product_image = serializers.SerializerMethodField()

    class Meta:
        model = OrderedProduct
        fields = [
            'id', 'product_title', 'product_option_name', 'product_image',
            'quantity', 'product_price', 'tx_price', 'delivery_price',
            'status', 'created_at'
        ]

    def get_product_image(self, obj):
        if obj.product_option.images_set.exists():
            request = self.context.get('request')
            image_url = obj.product_option.images_set.first().image.url
            if request:
                return request.build_absolute_uri(image_url)
            return image_url
        return None


class VendorOrderSerializer(serializers.ModelSerializer):
    """Serializer for order list"""
    user_name = serializers.CharField(source='user.fullname', read_only=True)
    items_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id', 'user_name', 'items_count', 'tx_amount',
            'vendor_status', 'payment_mode', 'created_at'
        ]

    def get_items_count(self, obj):
        return obj.orders_set.count()


class VendorDashboardSerializer(serializers.Serializer):
    """Serializer for dashboard stats"""
    total_products = serializers.IntegerField()
    total_options = serializers.IntegerField()
    total_stock = serializers.IntegerField()
    low_stock_items = serializers.IntegerField()
    recent_orders = serializers.IntegerField()
    pending_orders = serializers.IntegerField()
    total_revenue = serializers.IntegerField()


class VendorOrderItemSerializer(serializers.ModelSerializer):
    """Serializer for order items with rental information"""
    product_title = serializers.CharField(source='product_option.product.title', read_only=True)
    product_option_name = serializers.CharField(source='product_option.option', read_only=True)
    product_image = serializers.SerializerMethodField()

    class Meta:
        model = OrderedProduct
        fields = [
            'id', 'product_title', 'product_option_name', 'product_image',
            'quantity', 'product_price', 'tx_price', 'delivery_price',
            'status', 'created_at', 'rental_type', 'rental_duration',
            'rental_start_date', 'rental_end_date'
        ]

    def get_product_image(self, obj):
        """Get first image of the product option"""
        first_image = obj.product_option.images_set.first()
        if first_image and first_image.image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(first_image.image.url)
            return first_image.image.url
        return None


class VendorOrderUserSerializer(serializers.ModelSerializer):
    """Serializer for order user information"""
    class Meta:
        model = User
        fields = ['fullname', 'email', 'phone']


class VendorOrderListSerializer(serializers.ModelSerializer):
    """Serializer for vendor order list view"""
    user = VendorOrderUserSerializer(read_only=True)
    items = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id', 'user', 'items', 'tx_amount', 'payment_mode',
            'tx_status', 'vendor_status', 'address', 'vendor_notes',
            'created_at', 'vendor_accepted_at', 'latitude', 'longitude'
        ]

    def get_items(self, obj):
        """Get only vendor's items from this order"""
        request = self.context.get('request')
        vendor = request.user if request else None

        if vendor:
            # Filter items by vendor's products
            vendor_product_ids = vendor.products_set.values_list('id', flat=True)
            vendor_items = obj.orders_set.filter(
                product_option__product_id__in=vendor_product_ids
            )
            return VendorOrderItemSerializer(
                vendor_items,
                many=True,
                context=self.context
            ).data
        return []


class VendorOrderDetailSerializer(VendorOrderListSerializer):
    """Serializer for detailed vendor order view"""

    class Meta(VendorOrderListSerializer.Meta):
        fields = VendorOrderListSerializer.Meta.fields + [
            'tx_id', 'vendor_rejected_at'
        ]


class VendorOrderActionSerializer(serializers.Serializer):
    """Serializer for accept/reject actions"""
    notes = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=1000)

    def validate_reason(self, value):
        """Validate that reason is provided for rejection"""
        action = self.context.get('action')
        if action == 'reject' and not value:
            raise serializers.ValidationError("Rejection reason is required")
        return value


class ServiceableLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceableLocation
        fields = [
            'id', 'pincode', 'area_name', 'city', 'state',
            'is_active', 'rent_available', 'service_available',
            'delivery_charge', 'delivery_time'
        ]


class LocationCheckResponseSerializer(serializers.Serializer):
    """Response for location check"""
    is_serviceable = serializers.BooleanField()
    pincode = serializers.CharField()
    location_info = ServiceableLocationSerializer(required=False, allow_null=True)
    message = serializers.CharField()
    rent_available = serializers.BooleanField()
    service_available = serializers.BooleanField()


# serializers.py - Add these serializers

class HomePageItemSerializer(serializers.ModelSerializer):
    """Serializer for home page items"""
    items = serializers.SerializerMethodField()
    category_name = serializers.SerializerMethodField()
    total_items = serializers.SerializerMethodField()

    class Meta:
        model = HomePageItem
        fields = [
            'id', 'title', 'subtitle', 'item_type', 'position', 'viewtype',
            'image', 'category_name', 'items', 'total_items'
        ]

    def get_category_name(self, obj):
        if obj.item_type == 'rent' and obj.category:
            return obj.category.name
        elif obj.item_type == 'service' and obj.service_category:
            return obj.service_category.name
        return None

    def get_total_items(self, obj):
        return obj.get_items_count()

    def get_items(self, obj):
        """Get items based on type and apply limits"""
        request = self.context.get('request')

        # Apply limits based on viewtype
        if obj.viewtype == 3:  # GRID
            limit = 4
        elif obj.viewtype == 2:  # SWIPER
            limit = 8
        else:  # BANNER
            limit = 20

        if obj.item_type == 'rent':
            items = obj.product_options.all()[:limit]
            return self._serialize_product_items(items, request)
        else:  # service
            items = obj.service_options.all()[:limit]
            return self._serialize_service_items(items, request)

    def _serialize_product_items(self, items, request):
        """Serialize product items with rental pricing. Use option rent price (200) for cards, not buy price (7500)."""
        data = []
        for option in items:
            first_image = option.images_set.first()
            image_url = None
            if first_image and first_image.image:
                try:
                    image_url = request.build_absolute_uri(first_image.image.url) if request else first_image.image.url
                except Exception:
                    pass

            # Use option-level rent price/offer for card (Option price: 200, Option offer price: 150), not buy price
            price_val = option.get_price() or 0
            offer_val = option.get_offer_price() or 0
            effective_price = offer_val if (offer_val > 0 and price_val and offer_val < price_val) else price_val
            cutted_price = price_val if (offer_val > 0 and price_val and offer_val < price_val) else None
            discount_percentage = 0
            if cutted_price and price_val > 0:
                discount_percentage = round(((price_val - effective_price) / price_val) * 100)

            rental_price_1_day = option.get_rental_price('1_day')
            buy_price = option.get_buy_price()

            data.append({
                'id': str(option.product.id),
                'option_id': str(option.id),
                'product_option_id': str(option.id),
                'title': f"({option.option}) {option.product.title}" if option.option else option.product.title,
                'price': price_val,
                'offer_price': offer_val,
                'effective_price': effective_price,
                'cutted_price': cutted_price,
                'discount_percentage': discount_percentage,
                'option_price': option.option_price if option.option_price > 0 else None,
                'buy_price': buy_price,
                'image': image_url,
                'quantity_available': option.quantity,

                'rental_price_per_day': rental_price_1_day,
                'buy_offer_price': option.get_buy_offer_price(),

                'rental_pricing': {
                    '1_day': option.get_rental_price('1_day'),
                    '2_days': option.get_rental_price('2_days'),
                    '3_days': option.get_rental_price('3_days'),
                    '7_days': option.get_rental_price('7_days'),
                    '14_days': option.get_rental_price('14_days'),
                    '30_days': option.get_rental_price('30_days'),
                }
            })
        return data

    def _serialize_service_items(self, items, request):
        """Serialize service items"""
        data = []
        for option in items:
            first_image = option.images_set.first()
            image_url = None
            if first_image and first_image.image:
                try:
                    image_url = request.build_absolute_uri(first_image.image.url) if request else first_image.image.url
                except:
                    pass

            data.append({
                'id': str(option.service.id),
                'option_id': str(option.id),
                'title': f"{option.option_name} - {option.service.title}",
                'price': option.price,
                'duration': option.duration,
                'image': image_url,
                'provider_name': option.service.provider_name,
                'rating': float(option.service.rating),
            })
        return data


# In serializers.py - Add these new serializers

class ServiceWishlistItemSerializer(serializers.ModelSerializer):
    """Serializer for service wishlist items"""
    service_id = serializers.UUIDField(source='service.id', read_only=True)
    service_name = serializers.CharField(source='service.title', read_only=True)
    option_name = serializers.CharField(source='service_option.option_name', read_only=True)
    provider_name = serializers.CharField(source='service.provider_name', read_only=True)
    price = serializers.IntegerField(source='service_option.price', read_only=True)
    duration = serializers.CharField(source='service_option.duration', read_only=True)
    rating = serializers.DecimalField(source='service.rating', max_digits=3, decimal_places=1, read_only=True)
    service_image = serializers.SerializerMethodField()

    class Meta:
        model = ServiceWishlistItem
        fields = [
            'id', 'service_id', 'service_name', 'option_name',
            'provider_name', 'price', 'duration', 'rating',
            'service_image', 'added_at'
        ]

    def get_service_image(self, obj):
        """Get first image of the service option"""
        first_image = obj.service_option.images_set.first()
        if first_image:
            request = self.context.get('request')
            return request.build_absolute_uri(first_image.image.url) if request else first_image.image.url
        return None


class ServiceWishlistResponseSerializer(serializers.Serializer):
    """Response for wishlist operations"""
    success = serializers.BooleanField()
    message = serializers.CharField()
    wishlist_items = ServiceWishlistItemSerializer(many=True, required=False)
    total_items = serializers.IntegerField(required=False)