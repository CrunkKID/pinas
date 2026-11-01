// PiNAS-App für Android. Gebaut wird auf GitHub (Workflow „PiNAS-App bauen“) – lokal reicht Android Studio.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "PiNAS"
include(":app")
