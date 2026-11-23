from django.conf import settings
from django.urls import include, path
from rest_framework.routers import SimpleRouter
from monitor import views

router = SimpleRouter()
for name, view in [("environments", views.EnvironmentView), ("teams", views.TeamView),
                   ("services", views.ServiceView), ("checks", views.RunView),
                   ("daily-health", views.StatView)]:
    router.register(name, view, basename=name)
urlpatterns = [path("api/", include(router.urls))]
if settings.DEMO_ENABLED:
    urlpatterns.append(path("demo/services/<int:pk>/health/", views.demo_health))
