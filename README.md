# AppsPorter
Tool for porting OEM specific apps on every devices without root. 

<p align="center">
  <img src="readme/apc.jpg" alt="AppsPorter" width="230"/>
</p>

Usage 
```bash
bash build_port.sh <apk>  <patch name> 
```

This patch will work only for specific oem that patch is selected.

> [!WARNING]
> Compatibility depends on the selected OEM patch. A patch designed for one OEM may not work correctly on another.

Apps that require System UI privilages cannot work here.

## Patch Structure

All patches are located inside:

```text
apc/patches/
```

Create your patch using the following structure:

```text
apc/patches/
└── patch_name/
    │
    ├── framework/
    │   ├── framework-res.apk
    │   └── *.jar
    │
    ├── lib/
    │   └── *.so
    │
    ├── lib64/
    │   └── *.so
    │
    └── mods/
        └── custom smali files
```

### `framework/`

Place only the required framework files here:

* Required `.jar` files
* `framework-res.apk`

> Do not include an entire firmware dump. Only add dependencies required by the target application.

### `lib/` and `lib64/`

Place required native libraries (`.so`) inside:

```text
lib/
```

for 32-bit libraries, or:

```text
lib64/
```

for 64-bit libraries.

### `mods/`

Place your custom smali modifications here.

The directory structure inside `mods/` should match the desired destination inside the decompiled APK.

Example:

```text
mods/
    └── com/
        └── example/
            └── CustomClass.smali
```

---

## AI Workflow (OPTIONAL)

If you want to use AI tools like Claude, Kimi, or any other AI coding agent, follow this workflow.

**Prompt:**

```text
Git clone https://github.com/SameerAlSahab/AppsPorter

Necessary tools, keys, and scripts are already included in the repository.

You are a professional Android reverse engineer. Your task is to maximize the success rate of porting the target OEM-specific application to the intended device without root.

Analyze the repository structure, existing OEM patches, build scripts, and available tools before making changes.

Here is the APK you need to port:
[APKMIRROR LINK / DIRECT DOWNLOAD LINK / CHAT ATTACHMENT]

If using APKMirror, check the scripts directory for available download scripts.

Analyze the APK's dependencies, framework requirements, native libraries, and smali code. Identify compatibility issues and implement the necessary fixes using the existing AppsPorter patch structure.

Create or update the appropriate OEM patch under apc/patches/. Include only the required framework dependencies, native libraries, and custom smali modifications. Avoid unnecessary firmware files or unrelated changes. Even though scripts does it all. But you have to recheck new smalis, remove dangerous checks that can lead the app to crash. 

Build the ported APK, sign it, inspect the build output, and resolve any errors you can identify. Validate the final APK as thoroughly as the available tools allow.

Document the changes made, dependencies added, known limitations, and any remaining issues.

Do not claim that the port works unless it has been successfully tested on the intended device. If device testing is unavailable, clearly state what remains unverified. If the app not boots, as we have in-app logcat, ask user to give logs, then continue to edit more until success. 
```

___ 

## Licensing

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.

- **[android-tools](https://github.com/nmeum/android-tools)** - Licensed under Apache License 2.0
- **[apktool](https://github.com/iBotPeaches/Apktool)** - Licensed under Apache License 2.0  
- **[smali](https://github.com/google/smali)** - Licensed under BSD 3-Clause License
---

## Contributing
Contributions are always welcome!

If you like this project, star it, fork it or contribute.

Take love from APC ❤️

## Credits
Sameer Al Sahab, reiryuki and you!!!
