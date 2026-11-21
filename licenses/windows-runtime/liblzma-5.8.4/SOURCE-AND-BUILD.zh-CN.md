# liblzma 5.8.4 候选来源与构建说明

本说明对应 DLL SHA-256 `ccb6a179acf35e704b2ffd7f232677982c4856bd1dda1341713a27518cf97ce1`。它是 MSYS2 mingw64 包 `mingw-w64-x86_64-xz 5.8.4-1` 的原始载荷，不是本项目自行编译或修改的 DLL。

- 二进制包：[MSYS2 官方页面](https://packages.msys2.org/packages/mingw-w64-x86_64-xz)及[固定版本下载](https://mirror.msys2.org/mingw/mingw64/mingw-w64-x86_64-xz-5.8.4-1-any.pkg.tar.zst)。整包 SHA-256：`2de0f26da60b7ff7aa192a226330d7de6b1099cb9b9f9de4145affb80146bbc8`，与官网值一致。
- 构建时间：2026-09-09 18:39:08 UTC。随附 `.PKGINFO`、`.BUILDINFO` 保留精确包名、工具链及构建任务来源；这不是本地重现构建的证明。
- [固定构建配方](https://github.com/msys2/MINGW-packages/blob/bcf3f933e6c743a5d58b563d0e7da81b32ae35ae/mingw-w64-xz/PKGBUILD)的 SHA-256 为 `b93950051015573095f0d9cd50428ed6dd80d726c2786a2eeef005257888b3ba`，与该包 `.BUILDINFO` 记录逐字节匹配。配方未声明额外补丁，mingw64 配置包含禁用 NLS；不能因此推定整套构建已可重复。
- 配方指定的[上游完整源码](https://github.com/tukaani-project/xz/releases/download/v5.8.4/xz-5.8.4.tar.xz) SHA-256：`4ce24038fd4221e0d13bc1a2de7a4db56e90b92b3bf75321f6c14be73f65de4b`。源码归档及[对应签名](https://github.com/tukaani-project/xz/releases/download/v5.8.4/xz-5.8.4.tar.xz.sig)已留存于配套审查材料，不作为运行文件。
- 源码 OpenPGP 签名已验证，指纹 `3690C240CE51B4670D30AD1C38EE757D69184620`，公钥来自 [Tukaani 官方网站](https://tukaani.org/misc/lasse_collin_pubkey.txt)，与配方及上游公布指纹一致。本地信任库未赋予额外身份信任。
- MSYS2 二进制包的 detached `.sig` 和 MSYS2 source-only tarball 下载返回 HTTP 402，未重试其他镜像。**上游源码签名验证不等于二进制包签名验证**。此 DLL 无嵌入式 Authenticode 签名。本说明没有宣称该签名缺口已解决。

上游 `COPYING` 将 liblzma 核心代码列为 0BSD，完整授权文本见同目录 `COPYING.0BSD`。整套 XZ 包含其他 GPL/LGPL 工具和构建文件，它们没有作为本次运行时替换内容。此处材料不对所有编译器/CRT 成分或其他捆绑库的许可证作统一结论。

本次仅完成静态核对：x64/MSVCRT 及 114 个导出名称、序数匹配，普通导入无新增，并在确切 DLL 字节中确认 GHSA-5qpq-xqfv-j9pg 的两项修复。没有执行 Windows/DLL/Java，也没有完成 Windows 兼容性或完整安全验收。
