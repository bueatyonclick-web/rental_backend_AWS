# Fixed views.py - API endpoint improvements

import base64
import datetime
import hashlib
import hmac
import json
import logging
import math

import razorpay
import requests
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from django.db import transaction
from django.contrib.auth.hashers import make_password, check_password
from django.db.models import Q, Case, When, IntegerField, Value, F, Count, Avg, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import get_template
from google.auth import jwt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.generics import get_object_or_404
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.views import APIView

from django.utils.html import strip_tags
from django.utils.encoding import force_str

from backend.models import User, Otp, Token, Category, Slide, Product, PageItem, ProductOption, Coupon, Order, \
    OrderedProduct, Artist, Booking, BookingHistory, BeautyService
from backend.serializers import UserSerializer, CategorySerializer, SlideSerializer, PageItemSerializer, \
    ProductSerializer, WishlistSerializer, CartSerializer, AddressSerializer, ItemOrderSerializer, \
    OrderDetailsSerializer, NotificationSerializer, OrderItemSerializer, ProductOptionSerializer, InformMeSerializer, \
    VersionCheckRequestSerializer, \
    ArtistSerializer, BookingListSerializer, BookingDetailSerializer, BookingCreateSerializer, BookingRatingSerializer, \
    BeautyServiceSerializer, RescheduleBookingSerializer, CancelBookingSerializer
from backend.utils import send_otp, token_response, send_password_reset_email, IsAuthenticatedUser, cfSignature, \
    send_user_notification
from core import settings
from core.settings import TEMPLATES_BASE_URL, CF_ID, CF_KEY, RAZORPAY_KEY_ID, \
    RAZORPAY_KEY_SECRET
from rest_framework import status as http_status

from . import models
from .serializers import PrivacyPolicySerializer

from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


# Authentication APIs (existing code kept intact)
@api_view(['POST'])
def request_otp(request):
    email = request.data.get('email')
    phone = request.data.get('phone')

    if email and phone:
        if User.objects.filter(email=email).exists():
            return Response('email already exists', status=400)
        if User.objects.filter(phone=phone).exists():
            return Response('phone already exists', status=400)
        return send_otp(phone)
    else:
        return Response('data_missing', status=400)


@api_view(['POST'])
def resend_otp(request):
    phone = request.data.get('phone')
    if not phone:
        return Response('data_missing', 400)
    return send_otp(phone)


@api_view(['POST'])
def verify_otp(request):
    phone = request.data.get('phone')
    otp = request.data.get('otp')

    otp_obj = get_object_or_404(Otp, phone=phone, verified=False)

    if otp_obj.validity.replace(tzinfo=None) > datetime.datetime.utcnow():
        if otp_obj.otp == int(otp):
            otp_obj.verified = True
            otp_obj.save()
            return Response('otp_verified_successfully')
        else:
            return Response('Incorrect otp', 400)
    else:
        return Response('otp expired', 400)


@api_view(['POST'])
def create_account(request):
    email = request.data.get('email')
    phone = request.data.get('phone')
    password = request.data.get('password')
    fullname = request.data.get('fullname')
    fcmtoken = request.data.get('fcmtoken')

    if email and phone and password and fullname:
        otp_obj = get_object_or_404(Otp, phone=phone, verified=True)
        otp_obj.delete()

        user = User()
        user.email = email
        user.phone = phone
        user.fullname = fullname
        user.password = make_password(password)
        user.save()
        return token_response(user, fcmtoken)

    else:
        return Response('data_missing', 400)


@api_view(['POST'])
def login(request):
    email = request.data.get('email')
    phone = request.data.get('phone')
    password = request.data.get('password')
    fcmtoken = request.data.get('fcmtoken')

    if email:
        user = get_object_or_404(User, email=email)
    elif phone:
        user = get_object_or_404(User, phone=phone)
    else:
        return Response('data_missing', 400)

    if check_password(password, user.password):
        return token_response(user, fcmtoken)
    else:
        return Response('incorrect password', 400)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def logout(request):
    token_header = request.headers.get('Authorization')
    logout_all_param = request.GET.get('logout_all', 'false').lower()
    logout_all = logout_all_param == 'true'

    if logout_all:
        Token.objects.filter(user=request.user).delete()
    else:
        Token.objects.filter(token=token_header).delete()

    return Response({'message': 'logged_out'})


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def userdata(request):
    user = request.user
    data = UserSerializer(user, many=False).data
    return Response(data)


# NEW: Slides endpoint
@api_view(['GET'])
@permission_classes([AllowAny])
def slides(request):
    """
    Get all promotional slides ordered by position
    Public endpoint - no authentication required
    """
    try:
        slides_list = Slide.objects.all().order_by('position')

        slides_data = []
        for slide in slides_list:
            try:
                image_url = None
                if slide.image:
                    image_url = request.build_absolute_uri(slide.image.url) if request else slide.image.url

                slide_data = {
                    'id': slide.id,
                    'position': slide.position,
                    'image': image_url,
                }
                slides_data.append(slide_data)
            except Exception as e:
                print(f"Error processing slide {slide.id}: {e}")
                continue

        return Response(slides_data, status=200)

    except Exception as e:
        print(f"Error fetching slides: {e}")
        return Response([], status=200)


# Enhanced Home Screen API with slides integration
@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def home_screen_data(request):
    """
    Get all data needed for home screen including user info, categories,
    promotional slides, page items, and recommended products
    """
    user = request.user

    # Get user data
    user_data = UserSerializer(user, many=False).data

    # Get categories
    categories = Category.objects.filter().order_by('position')[:6]
    categories_data = CategorySerializer(categories, many=True, context={'request': request}).data

    # Get promotional slides
    try:
        slides = Slide.objects.all().order_by('position')
        slides_data = []

        for slide in slides:
            try:
                image_url = None
                if slide.image:
                    image_url = request.build_absolute_uri(slide.image.url) if request else slide.image.url

                slide_data = {
                    'id': slide.id,
                    'position': slide.position,
                    'image': image_url,
                }
                slides_data.append(slide_data)
            except Exception as e:
                print(f"Error processing slide {slide.id} in home API: {e}")
                continue
    except Exception as e:
        print(f"Error fetching slides in home API: {e}")
        slides_data = []

    # Enhanced page items with better product option handling
    page_items = PageItem.objects.select_related('category').prefetch_related(
        'product_options__product',
        'product_options__images_set'
    ).order_by('category__position', 'position')[:10]

    page_items_data = []
    for page_item in page_items:
        product_options_data = []
        for option in page_item.product_options.all()[:8]:
            first_image = option.images_set.first()
            image_url = None
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

            product_data = {
                'id': str(option.product.id),
                'title': f"({option.option}) {option.product.title}" if option.option else option.product.title,
                'price': option.product.price,
                'offer_price': option.product.offer_price,
                'image': image_url,
                'option_id': str(option.id),
                'option_name': option.option,
                'quantity_available': option.quantity,
            }
            product_options_data.append(product_data)

        page_item_data = {
            'id': page_item.id,
            'title': page_item.title,
            'position': page_item.position,
            'viewtype': page_item.viewtype,
            'image': request.build_absolute_uri(page_item.image.url) if page_item.image and request else (
                page_item.image.url if page_item.image else None),
            'category': page_item.category.name,
            'category_id': page_item.category.id,
            'product_options': product_options_data
        }
        page_items_data.append(page_item_data)

    # Enhanced recommended products with image handling
    recommended_products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(
        options_set__quantity__gt=0
    ).order_by('-created_at')[:6]

    products_data = []
    for product in recommended_products:
        first_option = product.options_set.first()
        first_image = None
        image_url = None

        if first_option:
            first_image = first_option.images_set.first()
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

        product_data = {
            'id': str(product.id),
            'title': product.title,
            'description': product.description,
            'price': product.price,
            'offer_price': product.offer_price,
            'delivery_charge': product.delivery_charge,
            'cod': product.cod,
            'category_name': product.category.name if product.category else None,
            'created_at': product.created_at.isoformat(),
            'updated_at': product.updated_at.isoformat(),
            'image': image_url,
            'options': []
        }

        for option in product.options_set.all():
            option_images = []
            for img in option.images_set.all():
                img_url = request.build_absolute_uri(img.image.url) if request else img.image.url
                option_images.append({
                    'position': img.position,
                    'image': img_url,
                    'product_option': str(option.id)
                })

            option_data = {
                'id': str(option.id),
                'option': option.option,
                'quantity': option.quantity,
                'images': option_images
            }
            product_data['options'].append(option_data)

        products_data.append(product_data)

    # Get cart count for user
    cart_count = user.cart.count()

    # Get wishlist count
    wishlist_count = user.wishlist.count()

    return Response({
        'user': user_data,
        'categories': categories_data,
        'promotional_slides': slides_data,
        'page_items': page_items_data,
        'recommended_products': products_data,
        'cart_count': cart_count,
        'wishlist_count': wishlist_count,
        'unread_notifications': user_data['notifications']
    })


# Search and filtering APIs
@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def search_products(request):
    """
    Enhanced product search with advanced filtering, sorting, and pagination
    """
    query = request.GET.get('q', '').strip()
    category_id = request.GET.get('category', None)
    min_price = request.GET.get('min_price', None)
    max_price = request.GET.get('max_price', None)
    sort_by = request.GET.get('sort', 'relevance')
    page = request.GET.get('page', 1)

    products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(options_set__quantity__gt=0).distinct()

    if query:
        products = products.filter(
            Q(title__icontains=query) |
            Q(description__icontains=query) |
            Q(category__name__icontains=query) |
            Q(options_set__option__icontains=query)
        ).distinct()

    if category_id:
        try:
            products = products.filter(category_id=int(category_id))
        except (ValueError, TypeError):
            pass

    if min_price:
        try:
            min_price_val = float(min_price)
            products = products.filter(
                Q(offer_price__gte=min_price_val, offer_price__gt=0) |
                Q(price__gte=min_price_val, offer_price__lte=0) |
                Q(price__gte=min_price_val, offer_price__isnull=True)
            )
        except (ValueError, TypeError):
            pass

    if max_price:
        try:
            max_price_val = float(max_price)
            products = products.filter(
                Q(offer_price__lte=max_price_val, offer_price__gt=0) |
                Q(price__lte=max_price_val, offer_price__lte=0) |
                Q(price__lte=max_price_val, offer_price__isnull=True)
            )
        except (ValueError, TypeError):
            pass

    # Apply sorting
    if sort_by == 'price_low_high':
        products = products.annotate(
            effective_price=Case(
                When(offer_price__gt=0, then=F('offer_price')),
                default=F('price')
            )
        ).order_by('effective_price')
    elif sort_by == 'price_high_low':
        products = products.annotate(
            effective_price=Case(
                When(offer_price__gt=0, then=F('offer_price')),
                default=F('price')
            )
        ).order_by('-effective_price')
    elif sort_by == 'newest':
        products = products.order_by('-created_at')
    elif sort_by == 'popularity':
        products = products.annotate(
            total_ratings=F('star_5') + F('star_4') + F('star_3') + F('star_2') + F('star_1')
        ).order_by('-total_ratings')
    elif sort_by == 'discount':
        products = products.annotate(
            discount_percent=Case(
                When(
                    offer_price__gt=0,
                    offer_price__lt=F('price'),
                    then=(F('price') - F('offer_price')) * 100.0 / F('price')
                ),
                default=0
            )
        ).order_by('-discount_percent')
    else:
        products = products.order_by('-created_at')

    # Pagination
    paginator = Paginator(products, 20)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    products_data = []
    for product in page_obj:
        first_option = product.options_set.first()
        image_url = None

        if first_option:
            first_image = first_option.images_set.first()
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

        product_data = {
            'id': str(product.id),
            'title': product.title,
            'description': product.description,
            'price': product.price,
            'offer_price': product.offer_price,
            'delivery_charge': product.delivery_charge,
            'cod': product.cod,
            'category_name': product.category.name if product.category else None,
            'created_at': product.created_at.isoformat(),
            'updated_at': product.updated_at.isoformat(),
            'image': image_url,
            'options': []
        }

        for option in product.options_set.all():
            option_images = []
            for img in option.images_set.all():
                img_url = request.build_absolute_uri(img.image.url) if request else img.image.url
                option_images.append({
                    'position': img.position,
                    'image': img_url,
                    'product_option': str(option.id)
                })

            option_data = {
                'id': str(option.id),
                'option': option.option,
                'quantity': option.quantity,
                'images': option_images
            }
            product_data['options'].append(option_data)

        products_data.append(product_data)

    return Response({
        'products': products_data,
        'total_pages': paginator.num_pages,
        'current_page': page_obj.number,
        'total_products': paginator.count,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
        'query': query,
        'filters': {
            'category_id': category_id,
            'min_price': min_price,
            'max_price': max_price,
            'sort_by': sort_by,
        }
    })


# Cart and Wishlist APIs
@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_to_cart(request):
    user = request.user
    product_option_id = request.data.get('product_option_id')
    quantity = request.data.get('quantity', 1)

    if not product_option_id:
        return Response({'error': 'Product option ID is required'}, status=400)

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    if user.cart.filter(id=product_option_id).exists():
        return Response({'message': 'Product already in cart'}, status=200)

    if product_option.quantity < quantity:
        return Response({'error': 'Insufficient stock'}, status=400)

    user.cart.add(product_option)

    return Response({
        'message': 'Added to cart successfully',
        'cart_count': user.cart.count()
    })


@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def remove_from_cart(request, product_option_id):
    user = request.user

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    if not user.cart.filter(id=product_option_id).exists():
        return Response({'error': 'Product not in cart'}, status=400)

    user.cart.remove(product_option)

    return Response({
        'message': 'Removed from cart successfully',
        'cart_count': user.cart.count()
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_to_wishlist(request):
    user = request.user
    product_option_id = request.data.get('product_option_id')

    if not product_option_id:
        return Response({'error': 'Product option ID is required'}, status=400)

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    if user.wishlist.filter(id=product_option_id).exists():
        return Response({'message': 'Product already in wishlist'}, status=200)

    user.wishlist.add(product_option)

    return Response({
        'message': 'Added to wishlist successfully',
        'wishlist_count': user.wishlist.count()
    })


@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def remove_from_wishlist(request, product_option_id):
    user = request.user

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    if not user.wishlist.filter(id=product_option_id).exists():
        return Response({'error': 'Product not in wishlist'}, status=400)

    user.wishlist.remove(product_option)

    return Response({
        'message': 'Removed from wishlist successfully',
        'wishlist_count': user.wishlist.count()
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_cart_items(request):
    user = request.user
    cart_items = user.cart.select_related('product').prefetch_related('images_set').all()

    cart_data = []
    for item in cart_items:
        first_image = item.images_set.first()
        image_url = None
        if first_image and request:
            image_url = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            image_url = first_image.image.url

        item_data = {
            'id': str(item.id),
            'title': f"({item.option}) {item.product.title}" if item.option else item.product.title,
            'image': image_url,
            'price': item.product.price,
            'offer_price': item.product.offer_price,
            'quantity': item.quantity,
            'cod': item.product.cod,
            'delivery_charge': item.product.delivery_charge,
        }
        cart_data.append(item_data)

    total_amount = sum(item['price'] for item in cart_data)
    offer_amount = sum(item['offer_price'] for item in cart_data if item['offer_price'] > 0)
    delivery_charges = sum(item['delivery_charge'] for item in cart_data)

    final_amount = offer_amount if offer_amount > 0 else total_amount
    final_amount += delivery_charges

    return Response({
        'cart_items': cart_data,
        'total_amount': total_amount,
        'offer_amount': offer_amount,
        'delivery_charges': delivery_charges,
        'final_amount': final_amount,
        'total_items': len(cart_data)
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_wishlist_items(request):
    user = request.user
    wishlist_items = user.wishlist.select_related('product').prefetch_related('images_set').all()

    wishlist_data = []
    for item in wishlist_items:
        first_image = item.images_set.first()
        image_url = None
        if first_image and request:
            image_url = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            image_url = first_image.image.url

        item_data = {
            'id': str(item.product.id),
            'title': f"({item.option}) {item.product.title}" if item.option else item.product.title,
            'image': image_url,
            'price': item.product.price,
            'offer_price': item.product.offer_price,
        }
        wishlist_data.append(item_data)

    return Response({
        'wishlist_items': wishlist_data,
        'total_items': len(wishlist_data)
    })

# Fixed category products API
@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def category_products(request, category_id):
    """
    Enhanced category products with filtering and sorting
    """
    try:
        category = Category.objects.get(id=category_id)
    except Category.DoesNotExist:
        return Response({'error': 'Category not found'}, status=404)

    # Get query parameters
    sort_by = request.GET.get('sort', 'relevance')
    min_price = request.GET.get('min_price', None)
    max_price = request.GET.get('max_price', None)
    page = request.GET.get('page', 1)

    # Get products in this category with stock
    products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(
        category=category,
        options_set__quantity__gt=0
    ).distinct()

    # Apply price filters
    if min_price:
        try:
            min_price_val = float(min_price)
            products = products.filter(
                Q(offer_price__gte=min_price_val, offer_price__gt=0) |
                Q(price__gte=min_price_val, offer_price__lte=0) |
                Q(price__gte=min_price_val, offer_price__isnull=True)
            )
        except (ValueError, TypeError):
            pass

    if max_price:
        try:
            max_price_val = float(max_price)
            products = products.filter(
                Q(offer_price__lte=max_price_val, offer_price__gt=0) |
                Q(price__lte=max_price_val, offer_price__lte=0) |
                Q(price__lte=max_price_val, offer_price__isnull=True)
            )
        except (ValueError, TypeError):
            pass

    # Apply sorting (same logic as search_products)
    if sort_by == 'price_low_high':
        products = products.annotate(
            effective_price=Case(
                When(offer_price__gt=0, then=F('offer_price')),
                default=F('price')
            )
        ).order_by('effective_price')
    elif sort_by == 'price_high_low':
        products = products.annotate(
            effective_price=Case(
                When(offer_price__gt=0, then=F('offer_price')),
                default=F('price')
            )
        ).order_by('-effective_price')
    elif sort_by == 'newest':
        products = products.order_by('-created_at')
    elif sort_by == 'popularity':
        products = products.annotate(
            total_ratings=F('star_5') + F('star_4') + F('star_3') + F('star_2') + F('star_1')
        ).order_by('-total_ratings')
    elif sort_by == 'discount':
        products = products.annotate(
            discount_percent=Case(
                When(
                    offer_price__gt=0,
                    offer_price__lt=F('price'),
                    then=(F('price') - F('offer_price')) * 100.0 / F('price')
                ),
                default=0
            )
        ).order_by('-discount_percent')
    elif sort_by == 'rating':
        products = products.annotate(
            total_ratings=F('star_5') + F('star_4') + F('star_3') + F('star_2') + F('star_1'),
            avg_rating=Case(
                When(
                    total_ratings__gt=0,
                    then=(F('star_5') * 5 + F('star_4') * 4 + F('star_3') * 3 + F('star_2') * 2 + F('star_1') * 1) / F(
                        'total_ratings')
                ),
                default=0
            )
        ).order_by('-avg_rating')
    else:
        products = products.order_by('-created_at')

    # Pagination
    paginator = Paginator(products, 20)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # Enhanced product serialization
    products_data = []
    for product in page_obj:
        # Get first option and its first image
        first_option = product.options_set.first()
        image_url = None

        if first_option:
            first_image = first_option.images_set.first()
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

        product_data = {
            'id': str(product.id),
            'title': product.title,
            'description': product.description,
            'price': product.price,
            'offer_price': product.offer_price,
            'delivery_charge': product.delivery_charge,
            'cod': product.cod,
            'category_name': product.category.name if product.category else None,
            'created_at': product.created_at.isoformat(),
            'updated_at': product.updated_at.isoformat(),
            'image': image_url,
            'options': []
        }

        # Add options
        for option in product.options_set.all():
            option_images = []
            for img in option.images_set.all():
                img_url = request.build_absolute_uri(img.image.url) if request else img.image.url
                option_images.append({
                    'position': img.position,
                    'image': img_url,
                    'product_option': str(option.id)
                })

            option_data = {
                'id': str(option.id),
                'option': option.option,
                'quantity': option.quantity,
                'images': option_images
            }
            product_data['options'].append(option_data)

        products_data.append(product_data)

    # Serialize category data
    category_data = CategorySerializer(category, context={'request': request}).data

    return Response({
        'category': category_data,
        'products': products_data,
        'total_pages': paginator.num_pages,
        'current_page': page_obj.number,
        'total_products': paginator.count,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
        'filters': {
            'min_price': min_price,
            'max_price': max_price,
            'sort_by': sort_by,
        }
    })


# Add missing all_categories endpoint for ViewAll screen
@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def all_categories(request):
    """
    Get all categories with product counts for categories view
    """
    page = request.GET.get('page', 1)

    # Get categories with product counts
    categories = Category.objects.annotate(
        product_count=Count('products_set', distinct=True),
        in_stock_count=Count(
            'products_set__options_set',
            filter=Q(products_set__options_set__quantity__gt=0),
            distinct=True
        )
    ).filter(in_stock_count__gt=0).order_by('position', 'name')

    # Pagination
    paginator = Paginator(categories, 20)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # Prepare category data for ViewAll screen (formatted as products)
    categories_data = []
    for category in page_obj:
        category_data = {
            'id': str(category.id),
            'title': category.name,  # Use title for consistency with products
            'image': request.build_absolute_uri(category.image.url) if category.image and request else (
                category.image.url if category.image else None),
            'price': 0,  # Categories don't have prices
            'offer_price': 0,
            'category_data': {  # Nested category data for navigation
                'id': category.id,
                'name': category.name,
                'image': request.build_absolute_uri(category.image.url) if category.image and request else (
                    category.image.url if category.image else None),
                'product_count': category.in_stock_count,
            }
        }
        categories_data.append(category_data)

    return Response({
        'products': categories_data,  # Use 'products' key for consistency with ViewAllScreen
        'total_pages': paginator.num_pages,
        'current_page': page_obj.number,
        'total_products': paginator.count,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
    })


# Add missing page_item_products endpoint
@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def page_item_products(request):
    """
    Get products from specific page items with filtering and sorting
    """
    page_item_title = request.GET.get('title', '')
    category_id = request.GET.get('category_id', None)
    sort_by = request.GET.get('sort', 'relevance')
    min_price = request.GET.get('min_price', None)
    max_price = request.GET.get('max_price', None)
    page = request.GET.get('page', 1)

    # Start with empty queryset
    products = Product.objects.none()

    # Find page items by title and/or category
    page_items_query = PageItem.objects.select_related('category').prefetch_related(
        'product_options__product__options_set__images_set'
    )

    if page_item_title:
        page_items_query = page_items_query.filter(title__icontains=page_item_title)

    if category_id:
        try:
            page_items_query = page_items_query.filter(category_id=int(category_id))
        except (ValueError, TypeError):
            pass

    page_items = page_items_query.all()

    # Collect all product IDs from page items
    product_ids = set()
    for page_item in page_items:
        for option in page_item.product_options.all():
            product_ids.add(option.product.id)

    if product_ids:
        # Get products with stock
        products = Product.objects.select_related('category').prefetch_related(
            'options_set__images_set'
        ).filter(
            id__in=product_ids,
            options_set__quantity__gt=0
        ).distinct()

        # Apply price filters
        if min_price:
            try:
                min_price_val = float(min_price)
                products = products.filter(
                    Q(offer_price__gte=min_price_val, offer_price__gt=0) |
                    Q(price__gte=min_price_val, offer_price__lte=0) |
                    Q(price__gte=min_price_val, offer_price__isnull=True)
                )
            except (ValueError, TypeError):
                pass

        if max_price:
            try:
                max_price_val = float(max_price)
                products = products.filter(
                    Q(offer_price__lte=max_price_val, offer_price__gt=0) |
                    Q(price__lte=max_price_val, offer_price__lte=0) |
                    Q(price__lte=max_price_val, offer_price__isnull=True)
                )
            except (ValueError, TypeError):
                pass

        # Apply sorting
        if sort_by == 'price_low_high':
            products = products.annotate(
                effective_price=Case(
                    When(offer_price__gt=0, then=F('offer_price')),
                    default=F('price')
                )
            ).order_by('effective_price')
        elif sort_by == 'price_high_low':
            products = products.annotate(
                effective_price=Case(
                    When(offer_price__gt=0, then=F('offer_price')),
                    default=F('price')
                )
            ).order_by('-effective_price')
        elif sort_by == 'newest':
            products = products.order_by('-created_at')
        elif sort_by == 'discount':
            products = products.annotate(
                discount_percent=Case(
                    When(
                        offer_price__gt=0,
                        offer_price__lt=F('price'),
                        then=(F('price') - F('offer_price')) * 100.0 / F('price')
                    ),
                    default=0
                )
            ).order_by('-discount_percent')
        elif sort_by == 'rating':
            products = products.annotate(
                total_ratings=F('star_5') + F('star_4') + F('star_3') + F('star_2') + F('star_1'),
                avg_rating=Case(
                    When(
                        total_ratings__gt=0,
                        then=(F('star_5') * 5 + F('star_4') * 4 + F('star_3') * 3 + F('star_2') * 2 + F(
                            'star_1') * 1) / F('total_ratings')
                    ),
                    default=0
                )
            ).order_by('-avg_rating')
        else:
            products = products.order_by('-created_at')

    # Pagination
    paginator = Paginator(products, 20)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # Enhanced product serialization
    products_data = []
    for product in page_obj:
        # Get first option and its first image
        first_option = product.options_set.first()
        image_url = None

        if first_option:
            first_image = first_option.images_set.first()
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

        product_data = {
            'id': str(product.id),
            'title': product.title,
            'description': product.description,
            'price': product.price,
            'offer_price': product.offer_price,
            'delivery_charge': product.delivery_charge,
            'cod': product.cod,
            'category_name': product.category.name if product.category else None,
            'created_at': product.created_at.isoformat(),
            'updated_at': product.updated_at.isoformat(),
            'image': image_url,
            'options': []
        }

        # Add options
        for option in product.options_set.all():
            option_images = []
            for img in option.images_set.all():
                img_url = request.build_absolute_uri(img.image.url) if request else img.image.url
                option_images.append({
                    'position': img.position,
                    'image': img_url,
                    'product_option': str(option.id)
                })

            option_data = {
                'id': str(option.id),
                'option': option.option,
                'quantity': option.quantity,
                'images': option_images
            }
            product_data['options'].append(option_data)

        products_data.append(product_data)

    return Response({
        'products': products_data,
        'total_pages': paginator.num_pages,
        'current_page': page_obj.number,
        'total_products': paginator.count,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
        'page_item_title': page_item_title,
        'category_id': category_id,
        'filters': {
            'min_price': min_price,
            'max_price': max_price,
            'sort_by': sort_by,
        }
    })


#


# Fixed Cart and Wishlist Management APIs
@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_to_cart(request):
    """
    Add a product option to user's cart
    """
    user = request.user
    product_option_id = request.data.get('product_option_id')
    quantity = request.data.get('quantity', 1)

    if not product_option_id:
        return Response({'error': 'Product option ID is required'}, status=400)

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    # Check if product is already in cart
    if user.cart.filter(id=product_option_id).exists():
        return Response({'message': 'Product already in cart'}, status=200)

    # Check stock availability
    if product_option.quantity < quantity:
        return Response({'error': 'Insufficient stock'}, status=400)

    # Add to cart
    user.cart.add(product_option)

    return Response({
        'message': 'Added to cart successfully',
        'cart_count': user.cart.count()
    })


@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def remove_from_cart(request, product_option_id):
    """
    Remove a product option from user's cart
    """
    user = request.user

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    if not user.cart.filter(id=product_option_id).exists():
        return Response({'error': 'Product not in cart'}, status=400)

    user.cart.remove(product_option)

    return Response({
        'message': 'Removed from cart successfully',
        'cart_count': user.cart.count()
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_to_wishlist(request):
    """
    Add a product option to user's wishlist
    """
    user = request.user
    product_option_id = request.data.get('product_option_id')

    if not product_option_id:
        return Response({'error': 'Product option ID is required'}, status=400)

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    # Check if product is already in wishlist
    if user.wishlist.filter(id=product_option_id).exists():
        return Response({'message': 'Product already in wishlist'}, status=200)

    # Add to wishlist
    user.wishlist.add(product_option)

    return Response({
        'message': 'Added to wishlist successfully',
        'wishlist_count': user.wishlist.count()
    })


@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def remove_from_wishlist(request, product_option_id):
    """
    Remove a product option from user's wishlist
    """
    user = request.user

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    if not user.wishlist.filter(id=product_option_id).exists():
        return Response({'error': 'Product not in wishlist'}, status=400)

    user.wishlist.remove(product_option)

    return Response({
        'message': 'Removed from wishlist successfully',
        'wishlist_count': user.wishlist.count()
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_cart_items(request):
    """
    Get user's cart items with enhanced product data
    """
    user = request.user
    cart_items = user.cart.select_related('product').prefetch_related('images_set').all()

    cart_data = []
    for item in cart_items:
        # Get first image
        first_image = item.images_set.first()
        image_url = None
        if first_image and request:
            image_url = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            image_url = first_image.image.url

        item_data = {
            'id': str(item.id),
            'title': f"({item.option}) {item.product.title}" if item.option else item.product.title,
            'image': image_url,
            'price': item.product.price,
            'offer_price': item.product.offer_price,
            'quantity': item.quantity,
            'cod': item.product.cod,
            'delivery_charge': item.product.delivery_charge,
        }
        cart_data.append(item_data)

    # Calculate totals
    total_amount = sum(item['price'] for item in cart_data)
    offer_amount = sum(item['offer_price'] for item in cart_data if item['offer_price'] > 0)
    delivery_charges = sum(item['delivery_charge'] for item in cart_data)

    final_amount = offer_amount if offer_amount > 0 else total_amount
    final_amount += delivery_charges

    return Response({
        'cart_items': cart_data,
        'total_amount': total_amount,
        'offer_amount': offer_amount,
        'delivery_charges': delivery_charges,
        'final_amount': final_amount,
        'total_items': len(cart_data)
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_wishlist_items(request):
    """
    Get user's wishlist items with enhanced product data
    """
    user = request.user
    wishlist_items = user.wishlist.select_related('product').prefetch_related('images_set').all()

    wishlist_data = []
    for item in wishlist_items:
        # Get first image
        first_image = item.images_set.first()
        image_url = None
        if first_image and request:
            image_url = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            image_url = first_image.image.url

        item_data = {
            'id': str(item.product.id),
            'title': f"({item.option}) {item.product.title}" if item.option else item.product.title,
            'image': image_url,
            'price': item.product.price,
            'offer_price': item.product.offer_price,
        }
        wishlist_data.append(item_data)

    return Response({
        'wishlist_items': wishlist_data,
        'total_items': len(wishlist_data)
    })


# Missing view functions that were referenced in original code
@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_page_items_by_category(request, category_id):
    """
    Get page items for a specific category
    """
    try:
        category = Category.objects.get(id=category_id)
    except Category.DoesNotExist:
        return Response({'error': 'Category not found'}, status=404)

    page_items = PageItem.objects.select_related('category').prefetch_related(
        'product_options__product',
        'product_options__images_set'
    ).filter(category=category).order_by('position')

    page_items_data = []
    for page_item in page_items:
        # Get product options with their details
        product_options_data = []
        for option in page_item.product_options.all():
            # Get first image
            first_image = option.images_set.first()
            image_url = None
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

            product_data = {
                'id': str(option.product.id),
                'title': f"({option.option}) {option.product.title}" if option.option else option.product.title,
                'price': option.product.price,
                'offer_price': option.product.offer_price,
                'image': image_url,
            }
            product_options_data.append(product_data)

        page_item_data = {
            'id': page_item.id,
            'title': page_item.title,
            'position': page_item.position,
            'viewtype': page_item.viewtype,
            'image': request.build_absolute_uri(page_item.image.url) if page_item.image and request else (
                page_item.image.url if page_item.image else None),
            'category': page_item.category.name,
            'category_id': page_item.category.id,
            'product_options': product_options_data
        }
        page_items_data.append(page_item_data)

    category_data = CategorySerializer(category, context={'request': request}).data

    return Response({
        'category': category_data,
        'page_items': page_items_data
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_categories_with_page_items(request):
    """
    Get all categories with their page items
    """
    categories = Category.objects.prefetch_related(
        'pageitems_set__product_options__product',
        'pageitems_set__product_options__images_set'
    ).order_by('position')

    categories_data = []
    for category in categories:
        # Get page items for this category
        page_items = category.pageitems_set.order_by('position')
        page_items_data = []

        for page_item in page_items:
            # Get product options with their details
            product_options_data = []
            for option in page_item.product_options.all()[:8]:  # Limit to 8 products per page item
                # Get first image
                first_image = option.images_set.first()
                image_url = None
                if first_image and request:
                    image_url = request.build_absolute_uri(first_image.image.url)
                elif first_image:
                    image_url = first_image.image.url

                product_data = {
                    'id': str(option.product.id),
                    'title': f"({option.option}) {option.product.title}" if option.option else option.product.title,
                    'price': option.product.price,
                    'offer_price': option.product.offer_price,
                    'image': image_url,
                }
                product_options_data.append(product_data)

            page_item_data = {
                'id': page_item.id,
                'title': page_item.title,
                'position': page_item.position,
                'viewtype': page_item.viewtype,
                'image': request.build_absolute_uri(page_item.image.url) if page_item.image and request else (
                    page_item.image.url if page_item.image else None),
                'product_options': product_options_data
            }
            page_items_data.append(page_item_data)

        category_data = {
            'id': category.id,
            'name': category.name,
            'position': category.position,
            'image': request.build_absolute_uri(category.image.url) if category.image and request else (
                category.image.url if category.image else None),
            'page_items': page_items_data
        }
        categories_data.append(category_data)

    return Response({
        'categories': categories_data
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def product_details(request, product_id):
    """
    Get detailed product information including images, options, ratings, and user interaction status
    """
    try:
        product = Product.objects.select_related('category').prefetch_related(
            'options_set__images_set'
        ).get(id=product_id)
    except Product.DoesNotExist:
        return Response({'error': 'Product not found'}, status=404)

    user = request.user

    # Get all product options with their images
    options_data = []
    for option in product.options_set.all():
        # Get all images for this option
        option_images = []
        for img in option.images_set.order_by('position'):
            img_url = request.build_absolute_uri(img.image.url) if request else img.image.url
            option_images.append({
                'position': img.position,
                'image': img_url,
                'product_option_id': str(option.id)
            })

        option_data = {
            'id': str(option.id),
            'option': option.option,
            'quantity': option.quantity,
            'in_stock': option.quantity > 0,
            'images': option_images,
            # Check if this option is in user's cart or wishlist
            'in_cart': user.cart.filter(id=option.id).exists(),
            'in_wishlist': user.wishlist.filter(id=option.id).exists(),
        }
        options_data.append(option_data)

    # Calculate ratings and reviews summary
    total_ratings = product.star_5 + product.star_4 + product.star_3 + product.star_2 + product.star_1

    if total_ratings > 0:
        average_rating = (
                                 (product.star_5 * 5) +
                                 (product.star_4 * 4) +
                                 (product.star_3 * 3) +
                                 (product.star_2 * 2) +
                                 (product.star_1 * 1)
                         ) / total_ratings
        average_rating = round(average_rating, 1)
    else:
        average_rating = 0

    # Calculate discount percentage
    discount_percentage = 0
    if product.offer_price > 0 and product.offer_price < product.price:
        discount_percentage = round(((product.price - product.offer_price) / product.price) * 100)

    # Get the first available option's first image as main product image
    main_image = None
    first_option = product.options_set.first()
    if first_option:
        first_image = first_option.images_set.first()
        if first_image and request:
            main_image = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            main_image = first_image.image.url

    # Check if any option is in stock
    in_stock = product.options_set.filter(quantity__gt=0).exists()

    # Prepare product data
    product_data = {
        'id': str(product.id),
        'title': product.title,
        'description': product.description,
        'price': product.price,
        'offer_price': product.offer_price,
        'cutted_price': product.price if product.offer_price > 0 else None,  # Original price when there's an offer
        'effective_price': product.offer_price if product.offer_price > 0 else product.price,
        'delivery_charge': product.delivery_charge,
        'cod_available': product.cod,
        'discount_percentage': discount_percentage,
        'main_image': main_image,
        'category': {
            'id': product.category.id,
            'name': product.category.name,
        } if product.category else None,
        'in_stock': in_stock,
        'created_at': product.created_at.isoformat(),
        'updated_at': product.updated_at.isoformat(),

        # Ratings and Reviews
        'ratings': {
            'average_rating': average_rating,
            'total_reviews': total_ratings,
            'star_5': product.star_5,
            'star_4': product.star_4,
            'star_3': product.star_3,
            'star_2': product.star_2,
            'star_1': product.star_1,
        },

        # Product Options with Images
        'options': options_data,

        # User Interaction Status
        'user_status': {
            'has_in_cart': any(option['in_cart'] for option in options_data),
            'has_in_wishlist': any(option['in_wishlist'] for option in options_data),
        }
    }

    # Get related products from the same category (optional)
    related_products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(
        category=product.category,
        options_set__quantity__gt=0
    ).exclude(
        id=product.id
    ).distinct()[:6]

    related_products_data = []
    for related_product in related_products:
        # Get first option and its first image for related product
        first_option = related_product.options_set.first()
        image_url = None
        if first_option:
            first_image = first_option.images_set.first()
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

        related_data = {
            'id': str(related_product.id),
            'title': related_product.title,
            'price': related_product.price,
            'offer_price': related_product.offer_price,
            'image': image_url,
        }
        related_products_data.append(related_data)

    return Response({
        'product': product_data,
        'related_products': related_products_data,
        'success': True
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def apply_coupon(request):
    """
    Apply a coupon code to get discount
    """
    user = request.user
    coupon_code = request.data.get('coupon_code', '').strip().upper()

    if not coupon_code:
        return Response({'error': 'Coupon code is required'}, status=400)

    # Get user's cart items to calculate discount on
    cart_items = user.cart.select_related('product').all()

    if not cart_items:
        return Response({'error': 'Cart is empty'}, status=400)

    # Calculate cart total
    cart_total = sum(
        item.product.offer_price if item.product.offer_price > 0 else item.product.price
        for item in cart_items
    )

    # Define available coupons (you can move this to a database model later)
    available_coupons = {
        'WELCOME10': {'discount_percent': 10, 'min_amount': 500, 'max_discount': 200},
        'SAVE20': {'discount_percent': 20, 'min_amount': 1000, 'max_discount': 500},
        'FLAT50': {'discount_amount': 50, 'min_amount': 300, 'max_discount': 50},
        'NEWUSER': {'discount_percent': 15, 'min_amount': 800, 'max_discount': 300},
        'FESTIVAL25': {'discount_percent': 25, 'min_amount': 1500, 'max_discount': 750},
    }

    if coupon_code not in available_coupons:
        return Response({
            'success': False,
            'message': 'Invalid coupon code',
            'discount_amount': 0
        }, status=400)

    coupon = available_coupons[coupon_code]

    # Check minimum amount requirement
    if cart_total < coupon['min_amount']:
        return Response({
            'success': False,
            'message': f'Minimum order value of ₹{coupon["min_amount"]} required for this coupon',
            'discount_amount': 0
        }, status=400)

    # Calculate discount
    if 'discount_percent' in coupon:
        discount_amount = (cart_total * coupon['discount_percent']) / 100
    else:
        discount_amount = coupon.get('discount_amount', 0)

    # Apply maximum discount limit
    discount_amount = min(discount_amount, coupon['max_discount'])
    discount_amount = int(discount_amount)  # Convert to integer

    return Response({
        'success': True,
        'message': f'Coupon applied successfully! You saved ₹{discount_amount}',
        'discount_amount': discount_amount,
        'coupon_code': coupon_code
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def bulk_update_cart(request):
    """
    Update multiple cart items at once
    """
    user = request.user
    updates = request.data.get('updates', [])

    if not updates:
        return Response({'error': 'No updates provided'}, status=400)

    try:
        with transaction.atomic():
            for update in updates:
                product_option_id = update.get('product_option_id')
                quantity = update.get('quantity', 0)

                if not product_option_id:
                    continue

                try:
                    product_option = ProductOption.objects.get(id=product_option_id)
                except ProductOption.DoesNotExist:
                    continue

                if quantity <= 0:
                    # Remove item from cart
                    user.cart.remove(product_option)
                else:
                    # Check stock availability
                    if product_option.quantity < quantity:
                        return Response({
                            'error': f'Insufficient stock for {product_option.product.title}. Only {product_option.quantity} available.'
                        }, status=400)

                    # Add/update item in cart (since Django ManyToMany doesn't support quantity directly,
                    # you might need to create a separate CartItem model for quantities)
                    if not user.cart.filter(id=product_option_id).exists():
                        user.cart.add(product_option)

        # Return updated cart data
        return get_cart_items(request)

    except Exception as e:
        return Response({'error': str(e)}, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def cart_summary(request):
    """
    Get cart summary with item count and total value
    """
    user = request.user
    cart_items = user.cart.select_related('product').all()

    if not cart_items:
        return Response({
            'total_items': 0,
            'total_amount': 0,
            'cart_count': 0
        })

    total_amount = 0
    offer_amount = 0

    for item in cart_items:
        if item.product.offer_price > 0:
            offer_amount += item.product.offer_price
        else:
            total_amount += item.product.price

    final_amount = offer_amount if offer_amount > 0 else total_amount

    return Response({
        'total_items': len(cart_items),
        'total_amount': total_amount,
        'offer_amount': offer_amount,
        'final_amount': final_amount,
        'cart_count': len(cart_items)
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def clear_cart(request):
    """
    Clear all items from user's cart
    """
    user = request.user
    user.cart.clear()

    return Response({
        'message': 'Cart cleared successfully',
        'cart_count': 0
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def move_to_wishlist(request):
    """
    Move item from cart to wishlist
    """
    user = request.user
    product_option_id = request.data.get('product_option_id')

    if not product_option_id:
        return Response({'error': 'Product option ID is required'}, status=400)

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({'error': 'Product option not found'}, status=404)

    # Check if item is in cart
    if not user.cart.filter(id=product_option_id).exists():
        return Response({'error': 'Product not in cart'}, status=400)

    # Move from cart to wishlist
    user.cart.remove(product_option)

    if not user.wishlist.filter(id=product_option_id).exists():
        user.wishlist.add(product_option)

    return Response({
        'message': 'Item moved to wishlist successfully',
        'cart_count': user.cart.count(),
        'wishlist_count': user.wishlist.count()
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def validate_cart(request):
    """
    Validate cart items for stock availability and price changes
    """
    user = request.user
    cart_items = user.cart.select_related('product').all()

    if not cart_items:
        return Response({
            'valid': True,
            'message': 'Cart is empty',
            'issues': []
        })

    issues = []

    for item in cart_items:
        # Check if product is still available
        if item.quantity <= 0:
            issues.append({
                'product_option_id': str(item.id),
                'product_title': item.product.title,
                'issue': 'out_of_stock',
                'message': 'This item is now out of stock'
            })

        # Check if price has changed (you can implement price tracking if needed)
        # This would require storing original price when added to cart

    return Response({
        'valid': len(issues) == 0,
        'message': 'Cart validation complete' if len(issues) == 0 else f'{len(issues)} issues found',
        'issues': issues,
        'total_issues': len(issues)
    })


# Enhanced get_cart_items with better calculations
@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_cart_items_enhanced(request):
    """
    Enhanced version of get_cart_items with better data structure
    """
    user = request.user
    cart_items = user.cart.select_related('product').prefetch_related('images_set').all()

    cart_data = []
    total_amount = 0
    offer_amount = 0
    total_savings = 0
    delivery_charges = 0

    for item in cart_items:
        # Get first image
        first_image = item.images_set.first()
        image_url = None
        if first_image and request:
            image_url = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            image_url = first_image.image.url

        # Calculate prices
        original_price = item.product.price
        effective_price = item.product.offer_price if item.product.offer_price > 0 else item.product.price
        savings_per_item = original_price - effective_price

        # Add to totals (assuming quantity = 1 since your current model doesn't support quantities)
        total_amount += original_price
        offer_amount += effective_price
        total_savings += savings_per_item
        delivery_charges += item.product.delivery_charge

        item_data = {
            'id': str(item.id),
            'title': f"({item.option}) {item.product.title}" if item.option else item.product.title,
            'image': image_url,
            'price': original_price,
            'offer_price': effective_price,
            'quantity': 1,  # Since your model doesn't support quantities yet
            'cod': item.product.cod,
            'delivery_charge': item.product.delivery_charge,
            'savings': savings_per_item,
            'product_id': str(item.product.id),
            'in_stock': item.quantity > 0,
            'stock_quantity': item.quantity,
        }
        cart_data.append(item_data)

    # Calculate final amounts
    final_amount = offer_amount + delivery_charges

    # Check for free delivery threshold
    free_delivery_threshold = 500  # You can make this configurable
    if offer_amount >= free_delivery_threshold:
        delivery_charges = 0
        final_amount = offer_amount

    return Response({
        'cart_items': cart_data,
        'summary': {
            'total_amount': total_amount,
            'offer_amount': offer_amount,
            'total_savings': total_savings,
            'delivery_charges': delivery_charges,
            'final_amount': final_amount,
            'total_items': len(cart_data),
            'free_delivery_threshold': free_delivery_threshold,
            'free_delivery_eligible': offer_amount >= free_delivery_threshold,
            'amount_for_free_delivery': max(0, free_delivery_threshold - offer_amount)
        },
        'recommendations': {
            'similar_products': [],  # You can implement product recommendations
            'frequently_bought_together': []  # You can implement this feature
        }
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def apply_coupon_db(request):
    """
    Apply a coupon code using database-stored coupons
    """
    user = request.user
    coupon_code = request.data.get('coupon_code', '').strip().upper()

    if not coupon_code:
        return Response({'error': 'Coupon code is required'}, status=400)

    # Get user's cart items
    cart_items = user.cart.select_related('product').all()

    if not cart_items:
        return Response({'error': 'Cart is empty'}, status=400)

    # Calculate cart total
    cart_total = sum(
        item.product.offer_price if item.product.offer_price > 0 else item.product.price
        for item in cart_items
    )

    try:
        coupon = Coupon.objects.get(code=coupon_code)
    except Coupon.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Invalid coupon code',
            'discount_amount': 0
        }, status=400)

    # Validate coupon
    is_valid, message = coupon.is_valid(user=user, cart_amount=cart_total)

    if not is_valid:
        return Response({
            'success': False,
            'message': message,
            'discount_amount': 0
        }, status=400)

    # Calculate discount
    discount_amount = coupon.calculate_discount(cart_total)

    return Response({
        'success': True,
        'message': f'Coupon applied successfully! You saved ₹{discount_amount}',
        'discount_amount': int(discount_amount),
        'coupon_code': coupon_code,
        'coupon_details': {
            'name': coupon.name,
            'description': coupon.description,
            'discount_type': coupon.discount_type,
            'discount_value': coupon.discount_value,
        }
    })


# Add these to your views.py file

@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_order_tracking(request, order_id):
    """
    Get detailed tracking information for a specific order
    """
    user = request.user

    try:
        order = Order.objects.select_related('user').get(id=order_id, user=user)
    except Order.DoesNotExist:
        return Response({'error': 'Order not found'}, status=404)

    # Get ordered products for this order
    ordered_products = OrderedProduct.objects.select_related(
        'product_option__product'
    ).prefetch_related(
        'product_option__images_set'
    ).filter(order=order)

    # Create tracking timeline based on order status
    tracking_steps = _generate_tracking_timeline(order, ordered_products)

    # Get delivery partner info (you can customize this based on your delivery system)
    delivery_partner = _get_delivery_partner_info(order)

    # Serialize ordered products
    products_data = []
    for ordered_product in ordered_products:
        first_image = ordered_product.product_option.images_set.first()
        image_url = None
        if first_image and request:
            image_url = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            image_url = first_image.image.url

        product_data = {
            'id': str(ordered_product.id),
            'title': str(ordered_product.product_option),
            'image': image_url,
            'quantity': ordered_product.quantity,
            'price': ordered_product.product_price,
            'tx_price': ordered_product.tx_price,
            'status': ordered_product.status,
            'rating': ordered_product.rating,
        }
        products_data.append(product_data)

    return Response({
        'order': {
            'id': str(order.id),
            'order_number': f"BH{str(order.id)[:8].upper()}",
            'status': order.tx_status,
            'payment_mode': order.payment_mode,
            'total_amount': order.tx_amount,
            'created_at': order.created_at.isoformat(),
            'delivery_address': order.address,
            'expected_delivery': _calculate_expected_delivery(order),
        },
        'products': products_data,
        'tracking_timeline': tracking_steps,
        'delivery_partner': delivery_partner,
        'support_info': {
            'phone': '+91 98765 43210',
            'email': 'support@beautyhub.com',
            'chat_available': True,
        }
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_user_orders(request):
    """
    Get all orders for the authenticated user with basic tracking info
    """
    user = request.user
    page = request.GET.get('page', 1)
    status_filter = request.GET.get('status', None)

    # Get user's orders
    orders = Order.objects.select_related('user').prefetch_related(
        'orders_set__product_option__product',
        'orders_set__product_option__images_set'
    ).filter(user=user).order_by('-created_at')

    # Apply status filter if provided
    if status_filter:
        orders = orders.filter(tx_status=status_filter)

    # Pagination
    paginator = Paginator(orders, 10)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    orders_data = []
    for order in page_obj:
        # Get first product for display
        first_product = order.orders_set.first()
        product_image = None
        product_title = "Order Items"

        if first_product:
            product_title = str(first_product.product_option)
            first_image = first_product.product_option.images_set.first()
            if first_image and request:
                product_image = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                product_image = first_image.image.url

        # Calculate order summary
        total_items = order.orders_set.count()
        current_status = _get_current_tracking_status(order)

        order_data = {
            'id': str(order.id),
            'order_number': f"BH{str(order.id)[:8].upper()}",
            'status': order.tx_status,
            'current_status': current_status,
            'total_amount': order.tx_amount,
            'total_items': total_items,
            'created_at': order.created_at.isoformat(),
            'expected_delivery': _calculate_expected_delivery(order),
            'product_preview': {
                'title': product_title,
                'image': product_image,
                'additional_items': max(0, total_items - 1)
            }
        }
        orders_data.append(order_data)

    return Response({
        'orders': orders_data,
        'pagination': {
            'total_pages': paginator.num_pages,
            'current_page': page_obj.number,
            'total_orders': paginator.count,
            'has_next': page_obj.has_next(),
            'has_previous': page_obj.has_previous(),
        }
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def update_order_rating(request, ordered_product_id):
    """
    Update rating for a delivered product
    """
    user = request.user
    rating = request.data.get('rating', 0)
    review = request.data.get('review', '')

    if not (1 <= rating <= 5):
        return Response({'error': 'Rating must be between 1 and 5'}, status=400)

    try:
        ordered_product = OrderedProduct.objects.select_related('order').get(
            id=ordered_product_id,
            order__user=user,
            status='DELIVERED'
        )
    except OrderedProduct.DoesNotExist:
        return Response({'error': 'Product not found or not delivered'}, status=404)

    # Update rating
    old_rating = ordered_product.rating
    ordered_product.rating = rating
    ordered_product.save()

    # Update product ratings
    product = ordered_product.product_option.product

    # Remove old rating if exists
    if old_rating > 0:
        if old_rating == 5:
            product.star_5 = max(0, product.star_5 - 1)
        elif old_rating == 4:
            product.star_4 = max(0, product.star_4 - 1)
        elif old_rating == 3:
            product.star_3 = max(0, product.star_3 - 1)
        elif old_rating == 2:
            product.star_2 = max(0, product.star_2 - 1)
        elif old_rating == 1:
            product.star_1 = max(0, product.star_1 - 1)

    # Add new rating
    if rating == 5:
        product.star_5 += 1
    elif rating == 4:
        product.star_4 += 1
    elif rating == 3:
        product.star_3 += 1
    elif rating == 2:
        product.star_2 += 1
    elif rating == 1:
        product.star_1 += 1

    product.save()

    return Response({
        'message': 'Rating updated successfully',
        'rating': rating,
        'product_id': str(product.id)
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def cancel_order(request, order_id):
    """
    Cancel an order if it's still cancellable
    """
    user = request.user
    cancellation_reason = request.data.get('reason', 'User requested cancellation')

    try:
        order = Order.objects.get(id=order_id, user=user)
    except Order.DoesNotExist:
        return Response({'error': 'Order not found'}, status=404)

    # Check if order is cancellable
    if order.tx_status not in ['INITIATED', 'PENDING', 'SUCCESS']:
        return Response({'error': 'Order cannot be cancelled at this stage'}, status=400)

    # Check if any products are already shipped
    shipped_products = order.orders_set.filter(status__in=['OUT_FOR_DELIVERY', 'DELIVERED'])
    if shipped_products.exists():
        return Response({'error': 'Order contains shipped items and cannot be cancelled'}, status=400)

    # Cancel the order
    order.tx_status = 'CANCELLED'
    order.save()

    # Cancel all ordered products
    order.orders_set.update(status='CANCELLED')

    # Send notification
    send_user_notification(
        user,
        "Order Cancelled",
        f"Your order {order.id} has been cancelled successfully.",
        None
    )

    return Response({
        'message': 'Order cancelled successfully',
        'order_id': str(order.id)
    })


# Helper functions
def _generate_tracking_timeline(order, ordered_products):
    """Generate tracking timeline based on order and product status"""
    from django.utils import timezone

    timeline = []
    now = timezone.now()

    # Order Placed
    timeline.append({
        'icon': 'check_circle',
        'title': 'Order Placed',
        'description': 'Your order has been placed successfully',
        'time': order.created_at.strftime("%d %b %Y, %I:%M %p"),
        'is_completed': True,
        'timestamp': order.created_at.isoformat()
    })

    # Order Confirmed
    confirmed_time = order.created_at + timezone.timedelta(minutes=30)
    timeline.append({
        'icon': 'inventory_2',
        'title': 'Order Confirmed',
        'description': 'Your order has been confirmed and is being prepared',
        'time': confirmed_time.strftime("%d %b %Y, %I:%M %p"),
        'is_completed': order.tx_status == 'SUCCESS',
        'timestamp': confirmed_time.isoformat()
    })

    # Check if any product is out for delivery
    out_for_delivery = ordered_products.filter(status='OUT_FOR_DELIVERY').exists()
    delivery_time = order.created_at + timezone.timedelta(days=1)

    timeline.append({
        'icon': 'local_shipping',
        'title': 'Out for Delivery',
        'description': 'Your order is on the way',
        'time': delivery_time.strftime("%d %b %Y, %I:%M %p"),
        'is_completed': out_for_delivery,
        'timestamp': delivery_time.isoformat()
    })

    # Delivered
    delivered = ordered_products.filter(status='DELIVERED').exists()
    expected_delivery = _calculate_expected_delivery(order)

    timeline.append({
        'icon': 'home',
        'title': 'Delivered',
        'description': 'Your order has been delivered',
        'time': expected_delivery,
        'is_completed': delivered,
        'timestamp': (order.created_at + timezone.timedelta(days=2)).isoformat()
    })

    return timeline


def _get_delivery_partner_info(order):
    """Get delivery partner information (customize based on your system)"""
    # This is mock data - integrate with your actual delivery partner API
    partners = [
        {
            'name': 'Raj Kumar',
            'phone': '+91 98765 43210',
            'vehicle_number': 'MH12AB1234',
            'rating': 4.8
        },
        {
            'name': 'Priya Sharma',
            'phone': '+91 87654 32109',
            'vehicle_number': 'DL08CD5678',
            'rating': 4.9
        },
        {
            'name': 'Amit Singh',
            'phone': '+91 76543 21098',
            'vehicle_number': 'KA03EF9012',
            'rating': 4.7
        }
    ]

    # Return a partner based on order ID (for consistency)
    partner_index = int(str(order.id)[-1]) % len(partners)
    return partners[partner_index]


def _calculate_expected_delivery(order):
    """Calculate expected delivery time"""
    from django.utils import timezone

    # Add 2 days for standard delivery
    expected = order.created_at + timezone.timedelta(days=2)
    return expected.strftime("%d %b %Y, %I:%M %p")


def _get_current_tracking_status(order):
    """Get current human-readable tracking status"""
    status_map = {
        'INITIATED': 'Order Initiated',
        'PENDING': 'Payment Pending',
        'SUCCESS': 'Order Confirmed',
        'CANCELLED': 'Order Cancelled',
        'FAILED': 'Payment Failed',
    }

    # Check ordered products status
    ordered_products = order.orders_set.all()
    if ordered_products.filter(status='DELIVERED').exists():
        return 'Delivered'
    elif ordered_products.filter(status='OUT_FOR_DELIVERY').exists():
        return 'Out for Delivery'
    elif ordered_products.filter(status='CANCELLED').exists():
        return 'Cancelled'
    else:
        return status_map.get(order.tx_status, 'Processing')


# Add these imports to your existing views.py imports
from backend.models import Slide
from backend.serializers import SlideSerializer


# Add this new slides endpoint function to your views.py
@api_view(['GET'])
@permission_classes([AllowAny])  # Public endpoint - no authentication required
def slides(request):
    """
    Get all promotional slides ordered by position
    """
    try:
        # Debug: Print slides count
        slides_count = Slide.objects.count()
        print(f"Total slides in database: {slides_count}")

        slides_list = Slide.objects.all().order_by('position')

        # Debug: Print each slide
        for slide in slides_list:
            print(f"Slide ID: {slide.id}, Position: {slide.position}, Image: {slide.image}")

        slides_data = []
        for slide in slides_list:
            try:
                # Build absolute URL for the image
                image_url = None
                if slide.image:
                    if request:
                        image_url = request.build_absolute_uri(slide.image.url)
                    else:
                        image_url = slide.image.url

                    print(f"Processing slide {slide.id}: Image URL = {image_url}")

                slide_data = {
                    'id': slide.id,
                    'position': slide.position,
                    'image': image_url,
                }
                slides_data.append(slide_data)
            except Exception as e:
                print(f"Error processing slide {slide.id}: {e}")
                continue

        print(f"Returning {len(slides_data)} slides")
        return Response(slides_data, status=200)

    except Exception as e:
        print(f"Error fetching slides: {e}")
        return Response([], status=200)


# Enhanced home_screen_data function with better slides integration
@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def home_screen_data_enhanced(request):
    """
    Enhanced home screen data with improved slides handling
    """
    user = request.user

    # Get user data
    user_data = UserSerializer(user, many=False).data

    # Get categories
    categories = Category.objects.filter().order_by('position')[:6]
    categories_data = CategorySerializer(categories, many=True, context={'request': request}).data

    # Get promotional slides with better error handling
    try:
        slides = Slide.objects.all().order_by('position')
        slides_data = []
        for slide in slides:
            try:
                slide_data = {
                    'position': slide.position,
                    'image': request.build_absolute_uri(slide.image.url) if slide.image and request else (
                        slide.image.url if slide.image else None),
                    'id': slide.id,
                }
                slides_data.append(slide_data)
            except Exception as e:
                print(f"Error processing slide {slide.id}: {e}")
                continue
    except Exception as e:
        print(f"Error fetching slides: {e}")
        slides_data = []

    # Enhanced page items with better product option handling
    page_items = PageItem.objects.select_related('category').prefetch_related(
        'product_options__product',
        'product_options__images_set'
    ).order_by('category__position', 'position')[:10]

    page_items_data = []
    for page_item in page_items:
        product_options_data = []
        for option in page_item.product_options.all()[:8]:
            # Get the first image for this option
            first_image = option.images_set.first()
            image_url = None
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

            product_data = {
                'id': str(option.product.id),  # Ensure string format
                'title': f"({option.option}) {option.product.title}" if option.option else option.product.title,
                'price': option.product.price,
                'offer_price': option.product.offer_price,
                'image': image_url,
                # Add product option details for cart/wishlist operations
                'option_id': str(option.id),
                'option_name': option.option,
                'quantity_available': option.quantity,
            }
            product_options_data.append(product_data)

        page_item_data = {
            'id': page_item.id,
            'title': page_item.title,
            'position': page_item.position,
            'viewtype': page_item.viewtype,
            'image': request.build_absolute_uri(page_item.image.url) if page_item.image and request else (
                page_item.image.url if page_item.image else None),
            'category': page_item.category.name,
            'category_id': page_item.category.id,
            'product_options': product_options_data
        }
        page_items_data.append(page_item_data)

    # Enhanced recommended products with image handling
    recommended_products = Product.objects.select_related('category').prefetch_related(
        'options_set__images_set'
    ).filter(
        options_set__quantity__gt=0
    ).order_by('-created_at')[:6]

    # Enhanced product serialization with direct image access
    products_data = []
    for product in recommended_products:
        # Get first option and its first image
        first_option = product.options_set.first()
        first_image = None
        image_url = None

        if first_option:
            first_image = first_option.images_set.first()
            if first_image and request:
                image_url = request.build_absolute_uri(first_image.image.url)
            elif first_image:
                image_url = first_image.image.url

        # Create enhanced product data structure
        product_data = {
            'id': str(product.id),
            'title': product.title,
            'description': product.description,
            'price': product.price,
            'offer_price': product.offer_price,
            'delivery_charge': product.delivery_charge,
            'cod': product.cod,
            'category_name': product.category.name if product.category else None,
            'created_at': product.created_at.isoformat(),
            'updated_at': product.updated_at.isoformat(),
            # Direct image access for easier frontend handling
            'image': image_url,
            'options': []
        }

        # Add options data
        for option in product.options_set.all():
            option_images = []
            for img in option.images_set.all():
                img_url = request.build_absolute_uri(img.image.url) if request else img.image.url
                option_images.append({
                    'position': img.position,
                    'image': img_url,
                    'product_option': str(option.id)
                })

            option_data = {
                'id': str(option.id),
                'option': option.option,
                'quantity': option.quantity,
                'images': option_images
            }
            product_data['options'].append(option_data)

        products_data.append(product_data)

    # Get cart count for user
    cart_count = user.cart.count()

    # Get wishlist count
    wishlist_count = user.wishlist.count()

    return Response({
        'user': user_data,
        'categories': categories_data,
        'promotional_slides': slides_data,  # Enhanced slides data
        'page_items': page_items_data,
        'recommended_products': products_data,
        'cart_count': cart_count,
        'wishlist_count': wishlist_count,
        'unread_notifications': user_data['notifications']
    })

# Alternative: Update your existing home_screen_data function
# Replace your existing home_screen_data function with home_screen_data_enhanced
# or modify your existing function to include the enhanced slides handling

# Add this to your views.py file

@api_view(['PUT', 'PATCH'])
@permission_classes([IsAuthenticatedUser])
def update_profile(request):
    """
    Update user profile information
    PUT: Complete profile update
    PATCH: Partial profile update
    """
    user = request.user

    try:
        # Get the data from request
        data = request.data

        # Validate email uniqueness if being updated
        new_email = data.get('email')
        if new_email and new_email != user.email:
            if User.objects.filter(email=new_email).exists():
                return Response({
                    'success': False,
                    'message': 'Email already exists'
                }, status=400)

        # Validate phone uniqueness if being updated
        new_phone = data.get('phone')
        if new_phone and new_phone != user.phone:
            if User.objects.filter(phone=new_phone).exists():
                return Response({
                    'success': False,
                    'message': 'Phone number already exists'
                }, status=400)

        # Update user fields
        if 'fullname' in data:
            user.fullname = data['fullname']
        if 'email' in data:
            user.email = data['email']
        if 'phone' in data:
            user.phone = data['phone']

        # Update address fields
        if 'name' in data:
            user.name = data['name']
        if 'address' in data:
            user.address = data['address']
        if 'pincode' in data:
            try:
                user.pincode = int(data['pincode']) if data['pincode'] else None
            except (ValueError, TypeError):
                pass
        if 'contact_no' in data:
            user.contact_no = data['contact_no']
        if 'district' in data:
            user.district = data['district']
        if 'state' in data:
            user.state = data['state']

        # Save the user
        user.save()

        # Return updated user data
        user_data = UserSerializer(user, many=False).data

        return Response({
            'success': True,
            'message': 'Profile updated successfully',
            'user': user_data
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to update profile: {str(e)}'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def upload_profile_image(request):
    """
    Upload user profile image
    """
    user = request.user

    try:
        if 'profile_image' not in request.FILES:
            return Response({
                'success': False,
                'message': 'No image file provided'
            }, status=400)

        image_file = request.FILES['profile_image']

        # Validate file size (max 5MB)
        if image_file.size > 5 * 1024 * 1024:
            return Response({
                'success': False,
                'message': 'Image file too large (max 5MB)'
            }, status=400)

        # Validate file type
        allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
        if image_file.content_type not in allowed_types:
            return Response({
                'success': False,
                'message': 'Invalid image format. Only JPEG, PNG, and WebP are allowed'
            }, status=400)

        # TODO: Save image to your preferred storage (local, S3, etc.)
        # For now, we'll just return success
        # In production, you would:
        # 1. Save the image to storage
        # 2. Update user model with image URL/path
        # 3. Return the image URL

        return Response({
            'success': True,
            'message': 'Profile image uploaded successfully',
            'image_url': 'path/to/uploaded/image.jpg'  # Replace with actual URL
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to upload image: {str(e)}'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_profile(request):
    """
    Get complete user profile information
    """
    user = request.user

    try:
        # Serialize user data with address information
        user_data = {
            'id': user.id,
            'fullname': user.fullname,
            'email': user.email,
            'phone': user.phone,
            'name': user.name or '',
            'address': user.address or '',
            'pincode': user.pincode,
            'contact_no': user.contact_no or '',
            'district': user.district or '',
            'state': user.state or '',
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'wishlist_count': user.wishlist.count(),
            'cart_count': user.cart.count(),
            'notifications': user.notifications_set.filter(seen=False).count(),
        }

        return Response({
            'success': True,
            'user': user_data
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to get profile: {str(e)}'
        }, status=500)


# Add these address management views to your views.py file

@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_addresses(request):
    """
    Get all addresses for the authenticated user
    URL: /api/addresses/
    """
    user = request.user

    try:
        # Since the current model stores only one address per user,
        # we'll return it as a single address in a list format
        addresses = []

        if user.name or user.address or user.contact_no:
            address_data = {
                'id': 1,  # Static ID since there's only one address per user
                'type': 'Home',  # Default type
                'name': user.name or user.fullname,
                'address': user.address or '',
                'contact_no': user.contact_no or user.phone,
                'pincode': user.pincode,
                'district': user.district or '',
                'state': user.state or '',
                'is_default': True,  # Always default since there's only one
                'created_at': user.created_at.isoformat() if user.created_at else None
            }
            addresses.append(address_data)

        return Response({
            'success': True,
            'addresses': addresses,
            'message': 'Addresses fetched successfully'
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to fetch addresses: {str(e)}',
            'addresses': []
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_address(request):
    """
    Add a new address for the user
    URL: /api/addresses/add/
    """
    user = request.user

    try:
        data = request.data

        # Validate required fields
        required_fields = ['name', 'address', 'contact_no']
        for field in required_fields:
            if not data.get(field):
                return Response({
                    'success': False,
                    'message': f'{field} is required'
                }, status=400)

        # Validate phone number
        contact_no = data.get('contact_no', '')
        if len(contact_no.replace('+91 ', '').replace(' ', '')) < 10:
            return Response({
                'success': False,
                'message': 'Contact number must be at least 10 digits'
            }, status=400)

        # Validate pincode if provided
        pincode = data.get('pincode')
        if pincode:
            try:
                pincode_int = int(pincode)
                if len(str(pincode_int)) != 6:
                    return Response({
                        'success': False,
                        'message': 'Pincode must be 6 digits'
                    }, status=400)
            except (ValueError, TypeError):
                return Response({
                    'success': False,
                    'message': 'Invalid pincode format'
                }, status=400)

        # Update user address fields
        user.name = data.get('name')
        user.address = data.get('address')
        user.contact_no = data.get('contact_no')
        user.pincode = int(pincode) if pincode else None
        user.district = data.get('district', '')
        user.state = data.get('state', '')
        user.save()

        # Return the created address
        address_data = {
            'id': 1,
            'type': data.get('type', 'Home'),
            'name': user.name,
            'address': user.address,
            'contact_no': user.contact_no,
            'pincode': user.pincode,
            'district': user.district,
            'state': user.state,
            'is_default': True,
            'created_at': user.created_at.isoformat() if user.created_at else None
        }

        return Response({
            'success': True,
            'message': 'Address added successfully',
            'address': address_data
        }, status=201)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to add address: {str(e)}'
        }, status=500)


@api_view(['PUT'])
@permission_classes([IsAuthenticatedUser])
def update_address(request, address_id):
    """
    Update an existing address
    URL: /api/addresses/<address_id>/update/
    """
    user = request.user

    try:
        data = request.data

        # Validate required fields
        required_fields = ['name', 'address', 'contact_no']
        for field in required_fields:
            if not data.get(field):
                return Response({
                    'success': False,
                    'message': f'{field} is required'
                }, status=400)

        # Validate phone number
        contact_no = data.get('contact_no', '')
        if len(contact_no.replace('+91 ', '').replace(' ', '')) < 10:
            return Response({
                'success': False,
                'message': 'Contact number must be at least 10 digits'
            }, status=400)

        # Validate pincode if provided
        pincode = data.get('pincode')
        if pincode:
            try:
                pincode_int = int(pincode)
                if len(str(pincode_int)) != 6:
                    return Response({
                        'success': False,
                        'message': 'Pincode must be 6 digits'
                    }, status=400)
            except (ValueError, TypeError):
                return Response({
                    'success': False,
                    'message': 'Invalid pincode format'
                }, status=400)

        # Update user address fields
        user.name = data.get('name')
        user.address = data.get('address')
        user.contact_no = data.get('contact_no')
        user.pincode = int(pincode) if pincode else None
        user.district = data.get('district', '')
        user.state = data.get('state', '')
        user.save()

        # Return the updated address
        address_data = {
            'id': int(address_id),
            'type': data.get('type', 'Home'),
            'name': user.name,
            'address': user.address,
            'contact_no': user.contact_no,
            'pincode': user.pincode,
            'district': user.district,
            'state': user.state,
            'is_default': True,
            'created_at': user.created_at.isoformat() if user.created_at else None
        }

        return Response({
            'success': True,
            'message': 'Address updated successfully',
            'address': address_data
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to update address: {str(e)}'
        }, status=500)


@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def delete_address(request, address_id):
    """
    Delete an address
    URL: /api/addresses/<address_id>/delete/
    """
    user = request.user

    try:
        # Clear address fields
        user.name = ''
        user.address = ''
        user.contact_no = ''
        user.pincode = None
        user.district = ''
        user.state = ''
        user.save()

        return Response({
            'success': True,
            'message': 'Address deleted successfully'
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to delete address: {str(e)}'
        }, status=500)


# Also add the profile edit endpoint for your repository
@api_view(['PUT'])
@permission_classes([IsAuthenticatedUser])
def edit_profile(request):
    """
    Edit user profile information
    URL: /api/profile/edit/
    """
    user = request.user

    try:
        data = request.data

        # Validate email uniqueness if being updated
        new_email = data.get('email')
        if new_email and new_email != user.email:
            if User.objects.filter(email=new_email).exists():
                return Response({
                    'success': False,
                    'message': 'Email already exists'
                }, status=400)

        # Validate phone uniqueness if being updated
        new_phone = data.get('phone')
        if new_phone and new_phone != user.phone:
            if User.objects.filter(phone=new_phone).exists():
                return Response({
                    'success': False,
                    'message': 'Phone number already exists'
                }, status=400)

        # Update profile fields
        if 'fullname' in data:
            user.fullname = data['fullname']
        if 'email' in data:
            user.email = data['email']
        if 'phone' in data:
            user.phone = data['phone']

        # Update address fields if provided
        if 'name' in data:
            user.name = data['name']
        if 'address' in data:
            user.address = data['address']
        if 'contact_no' in data:
            user.contact_no = data['contact_no']
        if 'pincode' in data:
            try:
                user.pincode = int(data['pincode']) if data['pincode'] else None
            except (ValueError, TypeError):
                pass
        if 'district' in data:
            user.district = data['district']
        if 'state' in data:
            user.state = data['state']

        user.save()

        # Return updated user data
        user_data = {
            'id': user.id,
            'fullname': user.fullname,
            'email': user.email,
            'phone': user.phone,
            'name': user.name or '',
            'address': user.address or '',
            'contact_no': user.contact_no or '',
            'pincode': user.pincode,
            'district': user.district or '',
            'state': user.state or '',
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'wishlist_count': user.wishlist.count(),
            'cart_count': user.cart.count(),
            'notifications': user.notifications_set.filter(seen=False).count(),
        }

        return Response({
            'success': True,
            'message': 'Profile updated successfully',
            'user': user_data
        }, status=200)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to update profile: {str(e)}'
        }, status=500)

#todo wishlist enhanced here

@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_wishlist_items_enhanced(request):
    """
    Get enhanced wishlist items with complete product details
    """
    user = request.user
    wishlist_items = user.wishlist.select_related('product__category').prefetch_related('images_set').all()

    wishlist_data = []
    for item in wishlist_items:
        # Get first image
        first_image = item.images_set.first()
        image_url = None
        if first_image and request:
            image_url = request.build_absolute_uri(first_image.image.url)
        elif first_image:
            image_url = first_image.image.url

        # Calculate ratings
        total_ratings = (item.product.star_5 + item.product.star_4 +
                         item.product.star_3 + item.product.star_2 + item.product.star_1)

        if total_ratings > 0:
            average_rating = (
                                     (item.product.star_5 * 5) + (item.product.star_4 * 4) +
                                     (item.product.star_3 * 3) + (item.product.star_2 * 2) +
                                     (item.product.star_1 * 1)
                             ) / total_ratings
            average_rating = round(average_rating, 1)
        else:
            average_rating = 0

        # Calculate discount percentage
        discount_percentage = 0
        if item.product.offer_price > 0 and item.product.offer_price < item.product.price:
            discount_percentage = round(((item.product.price - item.product.offer_price) / item.product.price) * 100)

        item_data = {
            'id': str(item.id),
            'product_id': str(item.product.id),
            'name': f"({item.option}) {item.product.title}" if item.option else item.product.title,
            'image': image_url,
            'price': item.product.offer_price if item.product.offer_price > 0 else item.product.price,
            'original_price': item.product.price,
            'offer_price': item.product.offer_price,
            'discount_percentage': discount_percentage,
            'rating': average_rating,
            'reviews': total_ratings,
            'in_stock': item.quantity > 0,
            'category': item.product.category.name if item.product.category else 'Other',
            'cod_available': item.product.cod,
            'delivery_charge': item.product.delivery_charge,
            'quantity_available': item.quantity,
            'in_cart': user.cart.filter(id=item.id).exists(),
            'created_at': item.product.created_at.isoformat(),
        }
        wishlist_data.append(item_data)

    return Response({
        'success': True,
        'wishlist_items': wishlist_data,
        'total_items': len(wishlist_data),
        'message': 'Wishlist items fetched successfully'
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def add_to_wishlist_enhanced(request):
    """
    Enhanced add to wishlist with proper validation
    """
    user = request.user
    product_option_id = request.data.get('product_option_id')

    if not product_option_id:
        return Response({
            'success': False,
            'message': 'Product option ID is required'
        }, status=400)

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product not found'
        }, status=404)

    # Check if already in wishlist
    if user.wishlist.filter(id=product_option_id).exists():
        return Response({
            'success': False,
            'message': 'Item already in wishlist'
        }, status=200)

    # Add to wishlist
    user.wishlist.add(product_option)

    return Response({
        'success': True,
        'message': 'Item added to wishlist successfully',
        'wishlist_count': user.wishlist.count()
    })


@api_view(['DELETE'])
@permission_classes([IsAuthenticatedUser])
def remove_from_wishlist_enhanced(request, product_option_id):
    """
    Enhanced remove from wishlist
    """
    user = request.user

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product not found'
        }, status=404)

    if not user.wishlist.filter(id=product_option_id).exists():
        return Response({
            'success': False,
            'message': 'Item not in wishlist'
        }, status=400)

    # Store item data for undo functionality
    removed_item = {
        'id': str(product_option.id),
        'name': str(product_option),
    }

    user.wishlist.remove(product_option)

    return Response({
        'success': True,
        'message': 'Item removed from wishlist',
        'wishlist_count': user.wishlist.count(),
        'removed_item': removed_item
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def move_wishlist_to_cart(request):
    """
    Move all available wishlist items to cart
    """
    user = request.user
    wishlist_items = user.wishlist.filter(quantity__gt=0).all()

    if not wishlist_items:
        return Response({
            'success': False,
            'message': 'No available items in wishlist to move to cart'
        }, status=400)

    moved_items = []
    already_in_cart = []

    with transaction.atomic():
        for item in wishlist_items:
            if not user.cart.filter(id=item.id).exists():
                user.cart.add(item)
                user.wishlist.remove(item)
                moved_items.append(str(item))
            else:
                user.wishlist.remove(item)
                already_in_cart.append(str(item))

    return Response({
        'success': True,
        'message': f'{len(moved_items)} items moved to cart successfully',
        'moved_items_count': len(moved_items),
        'already_in_cart_count': len(already_in_cart),
        'cart_count': user.cart.count(),
        'wishlist_count': user.wishlist.count()
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def share_wishlist(request):
    """
    Generate shareable wishlist link or data
    """
    user = request.user
    wishlist_items = user.wishlist.select_related('product').all()

    if not wishlist_items:
        return Response({
            'success': False,
            'message': 'Wishlist is empty'
        }, status=400)

    # Create shareable data
    share_data = {
        'user_name': user.fullname or user.email,
        'total_items': len(wishlist_items),
        'items': []
    }

    for item in wishlist_items:
        share_data['items'].append({
            'name': str(item),
            'price': item.product.offer_price if item.product.offer_price > 0 else item.product.price,
            'category': item.product.category.name if item.product.category else 'Other'
        })

    # In a real implementation, you might:
    # 1. Create a temporary shareable link with expiration
    # 2. Generate a shareable image
    # 3. Create deep links to your app

    return Response({
        'success': True,
        'message': 'Wishlist prepared for sharing',
        'share_data': share_data,
        'share_text': f"Check out {user.fullname or 'my'} wishlist with {len(wishlist_items)} amazing items!",
        'share_url': f"https://yourapp.com/shared-wishlist/{user.id}"  # Replace with actual URL
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def undo_wishlist_removal(request):
    """
    Undo the last wishlist item removal
    """
    user = request.user
    product_option_id = request.data.get('product_option_id')

    if not product_option_id:
        return Response({
            'success': False,
            'message': 'Product option ID is required'
        }, status=400)

    try:
        product_option = ProductOption.objects.get(id=product_option_id)
    except ProductOption.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Product not found'
        }, status=404)

    # Add back to wishlist
    if not user.wishlist.filter(id=product_option_id).exists():
        user.wishlist.add(product_option)

    return Response({
        'success': True,
        'message': 'Item restored to wishlist',
        'wishlist_count': user.wishlist.count()
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def clear_wishlist(request):
    """
    Clear all items from wishlist
    """
    user = request.user
    items_count = user.wishlist.count()

    if items_count == 0:
        return Response({
            'success': False,
            'message': 'Wishlist is already empty'
        }, status=400)

    user.wishlist.clear()

    return Response({
        'success': True,
        'message': f'All {items_count} items removed from wishlist',
        'wishlist_count': 0
    })


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_user_bookings(request):
    """
    Get user's bookings with filtering options
    Query params:
    - status: filter by booking status (active, past, all) - default: all
    - page: pagination page number - default: 1
    - limit: items per page - default: 10
    """
    user = request.user
    status_filter = request.GET.get('status', 'all')
    page = request.GET.get('page', 1)
    limit = int(request.GET.get('limit', 10))

    try:
        # Base queryset with optimized queries
        bookings = Booking.objects.select_related(
            'service', 'artist'
        ).prefetch_related(
            'rating'
        ).filter(user=user)

        # Apply status filter
        if status_filter == 'active':
            bookings = bookings.filter(
                status__in=['PENDING', 'CONFIRMED', 'IN_PROGRESS']
            )
        elif status_filter == 'past':
            bookings = bookings.filter(
                status__in=['COMPLETED', 'CANCELLED']
            )
        # 'all' shows everything

        # Order by scheduled date/time (upcoming first, then past)
        bookings = bookings.order_by(
            '-scheduled_date',
            '-scheduled_time'
        )

        # Pagination
        paginator = Paginator(bookings, limit)
        try:
            page_obj = paginator.page(page)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

        # Serialize data
        serializer = BookingListSerializer(
            page_obj,
            many=True,
            context={'request': request}
        )

        # Separate active and past bookings for response
        active_bookings = []
        past_bookings = []

        for booking_data in serializer.data:
            if booking_data['is_active']:
                active_bookings.append(booking_data)
            else:
                past_bookings.append(booking_data)

        return Response({
            'success': True,
            'active_bookings': active_bookings,
            'past_bookings': past_bookings,
            'pagination': {
                'current_page': page_obj.number,
                'total_pages': paginator.num_pages,
                'total_bookings': paginator.count,
                'has_next': page_obj.has_next(),
                'has_previous': page_obj.has_previous(),
            }
        })

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error fetching bookings: {str(e)}'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_booking_detail(request, booking_id):
    """
    Get detailed information about a specific booking
    """
    user = request.user

    try:
        booking = get_object_or_404(
            Booking.objects.select_related(
                'service', 'artist'
            ).prefetch_related(
                'rating', 'history'
            ),
            id=booking_id,
            user=user
        )

        serializer = BookingDetailSerializer(
            booking,
            context={'request': request}
        )

        return Response({
            'success': True,
            'booking': serializer.data
        })

    except Booking.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Booking not found'
        }, status=404)
    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error fetching booking details: {str(e)}'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def create_booking(request):
    """
    Create a new booking
    Required fields:
    - service_id: UUID of the beauty service
    - artist_id: UUID of the artist
    - scheduled_date: Date in YYYY-MM-DD format
    - scheduled_time: Time in HH:MM format
    - service_address: Full address for service location
    Optional fields:
    - latitude, longitude: GPS coordinates
    - customer_notes: Special requests or notes
    """
    try:
        serializer = BookingCreateSerializer(
            data=request.data,
            context={'request': request}
        )

        if serializer.is_valid():
            booking = serializer.save()

            # Send notification to user
            send_user_notification(
                booking.user,
                "Booking Created",
                f"Your booking for {booking.service.name} has been created successfully.",
                None
            )

            # Return created booking details
            detail_serializer = BookingDetailSerializer(
                booking,
                context={'request': request}
            )

            return Response({
                'success': True,
                'message': 'Booking created successfully',
                'booking': detail_serializer.data
            }, status=201)

        return Response({
            'success': False,
            'message': 'Validation failed',
            'errors': serializer.errors
        }, status=400)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error creating booking: {str(e)}'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def cancel_booking(request, booking_id):
    """
    Cancel a booking
    Required fields:
    - reason: Reason for cancellation
    Optional fields:
    - refund_requested: Boolean (default: true)
    """
    user = request.user

    try:
        booking = get_object_or_404(Booking, id=booking_id, user=user)

        serializer = CancelBookingSerializer(
            data=request.data,
            context={'booking': booking, 'request': request}
        )

        if serializer.is_valid():
            # Update booking status
            booking.status = 'CANCELLED'
            booking.cancelled_at = datetime.timezone.now()
            booking.save()

            # Create history entry
            BookingHistory.objects.create(
                booking=booking,
                action='CANCELLED',
                description=f"Cancelled by user: {serializer.validated_data['reason']}",
                performed_by=user
            )

            # Send notification
            send_user_notification(
                booking.user,
                "Booking Cancelled",
                f"Your booking {booking.booking_number} has been cancelled.",
                None
            )

            # TODO: Process refund if requested and payment was made
            if serializer.validated_data.get('refund_requested') and booking.payment_status == 'PAID':
                # Implement refund logic here
                pass

            return Response({
                'success': True,
                'message': 'Booking cancelled successfully'
            })

        return Response({
            'success': False,
            'message': 'Validation failed',
            'errors': serializer.errors
        }, status=400)

    except Booking.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Booking not found'
        }, status=404)
    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error cancelling booking: {str(e)}'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def reschedule_booking(request, booking_id):
    """
    Reschedule a booking
    Required fields:
    - new_date: New date in YYYY-MM-DD format
    - new_time: New time in HH:MM format
    Optional fields:
    - reason: Reason for rescheduling
    """
    user = request.user

    try:
        booking = get_object_or_404(Booking, id=booking_id, user=user)

        serializer = RescheduleBookingSerializer(
            data=request.data,
            context={'booking': booking, 'request': request}
        )

        if serializer.is_valid():
            # Store old date/time for history
            old_date = booking.scheduled_date
            old_time = booking.scheduled_time

            # Update booking details
            booking.scheduled_date = serializer.validated_data['new_date']
            booking.scheduled_time = serializer.validated_data['new_time']
            booking.status = 'CONFIRMED'  # Reset to confirmed after rescheduling
            booking.save()

            # Create history entry
            reason = serializer.validated_data.get('reason', 'Rescheduled by user')
            BookingHistory.objects.create(
                booking=booking,
                action='RESCHEDULED',
                description=f"Rescheduled from {old_date} {old_time.strftime('%H:%M')} to {booking.scheduled_date} {booking.scheduled_time.strftime('%H:%M')}. Reason: {reason}",
                performed_by=user
            )

            # Send notification
            send_user_notification(
                booking.user,
                "Booking Rescheduled",
                f"Your booking {booking.booking_number} has been rescheduled to {booking.scheduled_date} at {booking.scheduled_time.strftime('%I:%M %p')}.",
                None
            )

            # Return updated booking
            detail_serializer = BookingDetailSerializer(
                booking,
                context={'request': request}
            )

            return Response({
                'success': True,
                'message': 'Booking rescheduled successfully',
                'booking': detail_serializer.data
            })

        return Response({
            'success': False,
            'message': 'Validation failed',
            'errors': serializer.errors
        }, status=400)

    except Booking.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Booking not found'
        }, status=404)
    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error rescheduling booking: {str(e)}'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def rate_booking(request):
    """
    Rate and review a completed booking
    Required fields:
    - booking_id: UUID of the booking
    - overall_rating: Integer 1-5
    - review_text: String (can be empty)
    Optional fields:
    - service_quality: Integer 1-5
    - punctuality: Integer 1-5
    - professionalism: Integer 1-5
    - is_anonymous: Boolean (default: false)
    """
    try:
        serializer = BookingRatingSerializer(
            data=request.data,
            context={'request': request}
        )

        if serializer.is_valid():
            rating = serializer.save()

            # Send notification
            send_user_notification(
                rating.booking.user,
                "Thank You for Your Rating",
                f"Thank you for rating your booking {rating.booking.booking_number}!",
                None
            )

            return Response({
                'success': True,
                'message': 'Rating submitted successfully',
                'rating': {
                    'overall_rating': rating.overall_rating,
                    'review_text': rating.review_text
                }
            })

        return Response({
            'success': False,
            'message': 'Validation failed',
            'errors': serializer.errors
        }, status=400)

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error submitting rating: {str(e)}'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def contact_artist(request, booking_id):
    """
    Initiate contact with artist for a booking
    Returns artist contact information
    """
    user = request.user

    try:
        booking = get_object_or_404(Booking, id=booking_id, user=user)

        # In a real app, this might:
        # - Send notification to artist
        # - Create a chat session
        # - Send SMS/WhatsApp message
        # - Return artist contact details

        artist = booking.artist

        return Response({
            'success': True,
            'message': f'Contact initiated with {artist.name}',
            'artist_contact': {
                'name': artist.name,
                'phone': artist.phone,
                'email': artist.email if artist.email else None
            }
        })

    except Booking.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Booking not found'
        }, status=404)
    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error contacting artist: {str(e)}'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def book_again(request, booking_id):
    """
    Book the same service again based on a previous booking
    Required fields from request:
    - scheduled_date: New date in YYYY-MM-DD format
    - scheduled_time: New time in HH:MM format
    """
    user = request.user

    try:
        original_booking = get_object_or_404(Booking, id=booking_id, user=user)

        # Extract data from original booking
        new_booking_data = {
            'service_id': str(original_booking.service.id),
            'artist_id': str(original_booking.artist.id),
            'service_address': original_booking.service_address,
            'latitude': str(original_booking.latitude) if original_booking.latitude else None,
            'longitude': str(original_booking.longitude) if original_booking.longitude else None,
            'customer_notes': original_booking.customer_notes,
        }

        # Add new date/time from request
        new_booking_data.update(request.data)

        # Create new booking
        serializer = BookingCreateSerializer(
            data=new_booking_data,
            context={'request': request}
        )

        if serializer.is_valid():
            new_booking = serializer.save()

            # Add reference to original booking in history
            BookingHistory.objects.create(
                booking=new_booking,
                action='CREATED',
                description=f'Booking created as repeat of {original_booking.booking_number}',
                performed_by=user
            )

            # Send notification
            send_user_notification(
                new_booking.user,
                "Booking Created",
                f"Your repeat booking for {new_booking.service.name} has been created successfully.",
                None
            )

            detail_serializer = BookingDetailSerializer(
                new_booking,
                context={'request': request}
            )

            return Response({
                'success': True,
                'message': 'New booking created successfully',
                'booking': detail_serializer.data
            }, status=201)

        return Response({
            'success': False,
            'message': 'Validation failed',
            'errors': serializer.errors
        }, status=400)

    except Booking.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Original booking not found'
        }, status=404)
    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error creating repeat booking: {str(e)}'
        }, status=500)


# ====================================
# SUPPORTING ENDPOINTS
# ====================================

@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_available_services(request):
    """
    Get all available beauty services
    Optional query params:
    - category: Filter by category
    """
    try:
        category = request.GET.get('category')

        services = BeautyService.objects.filter(is_active=True)

        if category:
            services = services.filter(category=category)

        services = services.order_by('category', 'name')
        serializer = BeautyServiceSerializer(services, many=True)

        return Response({
            'success': True,
            'services': serializer.data
        })

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error fetching services: {str(e)}'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_available_artists(request):
    """
    Get available artists, optionally filtered by service
    Optional query params:
    - service_id: Filter artists by service specialization
    """
    service_id = request.GET.get('service_id')

    try:
        artists = Artist.objects.filter(is_available=True)

        if service_id:
            artists = artists.filter(specializations__id=service_id)

        artists = artists.order_by('-average_rating', 'name').distinct()
        serializer = ArtistSerializer(
            artists,
            many=True,
            context={'request': request}
        )

        return Response({
            'success': True,
            'artists': serializer.data
        })

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error fetching artists: {str(e)}'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_artist_availability(request, artist_id):
    """
    Get artist's availability for a specific date
    Required query param:
    - date: Date in YYYY-MM-DD format
    """
    date_str = request.GET.get('date')

    if not date_str:
        return Response({
            'success': False,
            'message': 'Date parameter is required'
        }, status=400)

    try:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
        artist = get_object_or_404(Artist, id=artist_id, is_available=True)

        # Get existing bookings for that date
        existing_bookings = Booking.objects.filter(
            artist=artist,
            scheduled_date=date,
            status__in=['PENDING', 'CONFIRMED', 'IN_PROGRESS']
        ).values_list('scheduled_time', 'duration_minutes')

        # Create list of unavailable time slots
        unavailable_slots = []
        for booking_time, duration in existing_bookings:
            start_time = datetime.combine(date, booking_time)
            end_time = start_time + datetime.timedelta(minutes=duration)
            unavailable_slots.append({
                'start': booking_time.strftime('%H:%M'),
                'end': end_time.time().strftime('%H:%M')
            })

        return Response({
            'success': True,
            'date': date_str,
            'artist': artist.name,
            'unavailable_slots': unavailable_slots
        })

    except ValueError:
        return Response({
            'success': False,
            'message': 'Invalid date format. Use YYYY-MM-DD'
        }, status=400)
    except Artist.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Artist not found'
        }, status=404)
    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error fetching availability: {str(e)}'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_booking_stats(request):
    """
    Get user's booking statistics
    """
    user = request.user

    try:
        stats = {
            'total_bookings': Booking.objects.filter(user=user).count(),
            'completed_bookings': Booking.objects.filter(
                user=user,
                status='COMPLETED'
            ).count(),
            'cancelled_bookings': Booking.objects.filter(
                user=user,
                status='CANCELLED'
            ).count(),
            'active_bookings': Booking.objects.filter(
                user=user,
                status__in=['PENDING', 'CONFIRMED', 'IN_PROGRESS']
            ).count(),
        }

        # Calculate total spent
        total_spent = Booking.objects.filter(
            user=user,
            status='COMPLETED'
        ).aggregate(
            total=Sum('total_amount')
        )['total'] or 0

        stats['total_spent'] = float(total_spent)

        # Get favorite service
        favorite_service = Booking.objects.filter(
            user=user
        ).values(
            'service__name'
        ).annotate(
            count=Count('service')
        ).order_by('-count').first()

        stats['favorite_service'] = favorite_service['service__name'] if favorite_service else None

        return Response({
            'success': True,
            'stats': stats
        })

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error fetching stats: {str(e)}'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_payment_history(request):
    """
    Get user's payment history with filtering and pagination
    Query params:
    - status: filter by payment status (success, failed, pending)
    - page: pagination page number
    - limit: items per page (default: 20)
    """
    user = request.user
    status_filter = request.GET.get('status', '').lower()
    page = request.GET.get('page', 1)
    limit = int(request.GET.get('limit', 20))

    try:
        # Get orders for this user
        orders = Order.objects.select_related('user').prefetch_related(
            'orders_set__product_option__product'
        ).filter(user=user).order_by('-created_at')

        # Apply status filter
        if status_filter:
            status_map = {
                'success': 'SUCCESS',
                'failed': 'FAILED',
                'pending': 'PENDING',
            }
            if status_filter in status_map:
                orders = orders.filter(tx_status=status_map[status_filter])

        # Pagination
        paginator = Paginator(orders, limit)
        try:
            page_obj = paginator.page(page)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

        # Serialize payment data
        payments = []
        for order in page_obj:
            # Get service names from ordered products
            services = []
            for ordered_product in order.orders_set.all():
                services.append(str(ordered_product.product_option))

            service_text = ', '.join(services[:2])  # First 2 services
            if len(services) > 2:
                service_text += f' +{len(services) - 2} more'

            payment_data = {
                'id': str(order.id),
                'order_id': f"BH{str(order.id)[:8].upper()}",
                'service': service_text,
                'amount': float(order.tx_amount),
                'date': order.created_at.isoformat(),
                'method': order.payment_mode or 'Not specified',
                'status': _get_payment_status_display(order.tx_status),
                'transaction_id': order.tx_id or 'N/A',
                'booking_id': str(order.id),
                'receipt_url': None,  # Add receipt URL generation logic if needed
            }
            payments.append(payment_data)

        return Response({
            'success': True,
            'payments': payments,
            'pagination': {
                'current_page': page_obj.number,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'has_next': page_obj.has_next(),
                'has_previous': page_obj.has_previous(),
            }
        })

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error fetching payment history: {str(e)}'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_payment_summary(request):
    """
    Get payment summary statistics for the user
    """
    user = request.user

    try:
        # Get all user orders
        all_orders = Order.objects.filter(user=user)

        # Calculate statistics
        total_spent = all_orders.filter(
            tx_status='SUCCESS'
        ).aggregate(
            total=Sum('tx_amount')
        )['total'] or 0

        total_orders = all_orders.count()

        successful_payments = all_orders.filter(tx_status='SUCCESS').count()
        failed_payments = all_orders.filter(tx_status='FAILED').count()
        pending_payments = all_orders.filter(tx_status='PENDING').count()

        # Get most used payment method
        payment_methods = all_orders.filter(
            tx_status='SUCCESS',
            payment_mode__isnull=False
        ).values('payment_mode').annotate(
            count=Count('payment_mode')
        ).order_by('-count')

        most_used_method = payment_methods.first()['payment_mode'] if payment_methods else None

        summary = {
            'total_spent': float(total_spent),
            'total_orders': total_orders,
            'successful_payments': successful_payments,
            'failed_payments': failed_payments,
            'pending_payments': pending_payments,
            'most_used_method': most_used_method,
        }

        return Response({
            'success': True,
            'summary': summary
        })

    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error fetching payment summary: {str(e)}'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticatedUser])
def get_payment_detail(request, payment_id):
    """
    Get detailed information about a specific payment
    """
    user = request.user

    try:
        order = Order.objects.select_related('user').prefetch_related(
            'orders_set__product_option__product'
        ).get(id=payment_id, user=user)

        # Get service names
        services = []
        for ordered_product in order.orders_set.all():
            services.append(str(ordered_product.product_option))

        service_text = ', '.join(services)

        payment_data = {
            'id': str(order.id),
            'order_id': f"BH{str(order.id)[:8].upper()}",
            'service': service_text,
            'amount': float(order.tx_amount),
            'date': order.created_at.isoformat(),
            'method': order.payment_mode or 'Not specified',
            'status': _get_payment_status_display(order.tx_status),
            'transaction_id': order.tx_id or 'N/A',
            'booking_id': str(order.id),
            'receipt_url': None,
            'address': order.address,
            'tx_time': order.tx_time,
            'tx_msg': order.tx_msg,
        }

        return Response({
            'success': True,
            'payment': payment_data
        })

    except Order.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Payment not found'
        }, status=404)
    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error fetching payment details: {str(e)}'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def download_receipt(request, payment_id):
    """
    Generate and download receipt for a payment
    """
    user = request.user

    try:
        order = Order.objects.get(id=payment_id, user=user, tx_status='SUCCESS')

        # Generate receipt data
        receipt_data = {
            'receipt_id': f"RCP{str(order.id)[:8].upper()}",
            'order_id': f"BH{str(order.id)[:8].upper()}",
            'transaction_id': order.tx_id or 'N/A',
            'amount': float(order.tx_amount),
            'date': order.created_at.isoformat(),
            'method': order.payment_mode or 'Not specified',
            'status': 'Success',
            'pdf_url': None,  # TODO: Generate actual PDF and return URL
        }

        # TODO: Implement actual PDF generation
        # You can use libraries like:
        # - reportlab
        # - WeasyPrint
        # - xhtml2pdf
        # Then upload to storage and return the URL

        return Response({
            'success': True,
            'receipt': receipt_data,
            'message': 'Receipt generated successfully'
        })

    except Order.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Payment not found or not successful'
        }, status=404)
    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error generating receipt: {str(e)}'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticatedUser])
def request_refund(request, payment_id):
    """
    Request a refund for a payment
    """
    user = request.user
    reason = request.data.get('reason', '')

    if not reason:
        return Response({
            'success': False,
            'message': 'Refund reason is required'
        }, status=400)

    try:
        order = Order.objects.get(
            id=payment_id,
            user=user,
            tx_status='SUCCESS'
        )

        # Check if refund is already requested
        if order.tx_status == 'REFUNDED':
            return Response({
                'success': False,
                'message': 'Refund already processed'
            }, status=400)

        # TODO: Implement actual refund logic
        # This would typically involve:
        # 1. Validating refund eligibility (time limits, etc.)
        # 2. Initiating refund with payment gateway
        # 3. Updating order status
        # 4. Sending notification to user

        # For now, just return success
        return Response({
            'success': True,
            'message': 'Refund request submitted successfully. It will be processed within 5-7 business days.'
        })

    except Order.DoesNotExist:
        return Response({
            'success': False,
            'message': 'Payment not found or not eligible for refund'
        }, status=404)
    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error requesting refund: {str(e)}'
        }, status=500)


# Helper function
def _get_payment_status_display(tx_status):
    """Convert transaction status to payment status display"""
    status_map = {
        'SUCCESS': 'Success',
        'FAILED': 'Failed',
        'PENDING': 'Pending',
        'INITIATED': 'Pending',
        'INCOMPLETE': 'Failed',
        'CANCELLED': 'Failed',
        'REFUNDED': 'Refunded',
    }
    return status_map.get(tx_status, 'Pending')