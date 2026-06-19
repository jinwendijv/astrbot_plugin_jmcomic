from astrbot.api.star import Star, Context
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api import logger
from astrbot.api import message_components as Comp
import asyncio
import re
import os
from pathlib import Path
from typing import List

try:
    import jmcomic
except ImportError:
    jmcomic = None

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


def natural_sort_key(path: Path) -> list:
    """自然排序键，保证章节和图片按数字顺序排列"""
    def convert(text: str):
        return int(text) if text.isdigit() else text.lower()
    return [convert(c) for c in re.split(r'(\d+)', str(path))]


class JMComicPlugin(Star):
    """JMComic 禁漫漫画插件 - AstrBot v4.x 标准实现"""
    
    def __init__(self, context: Context, config: dict = None):
        """✅ 多层保护：确保 config 永远不会是 None"""
        # ✅ 第一层：先保存，保证不是 None
        self.context = context
        self.config = config if config is not None else {}
        
        # ✅ 调用父类
        super().__init__(context, config)
        
        # ✅ 第二层：再次检查保护
        if self.config is None:
            self.config = {}
        
        self.plugin_path = Path(__file__).parent
        self.jm_client = None

    async def initialize(self):
        """✅ AstrBot 自动调用的初始化方法"""
        logger.info("JMComic 插件正在初始化...")
        
        # ✅ 第三层：终极保护 - 无论什么情况都保证 config 存在
        if not hasattr(self, 'config') or self.config is None:
            self.config = {}
        
        # ✅ 安全读取配置（永远不会报错）
        self.enable_whitelist = self.config.get("enable_whitelist", False)
        self.group_whitelist = self.config.get("group_whitelist", [])
        self.clean_temp_files = self.config.get("clean_temp_files", True)
        self.jm_username = self.config.get("jm_username", "")
        self.jm_password = self.config.get("jm_password", "")
        self.jm_domain = self.config.get("jm_domain", "18comic.vip")
        
        # 初始化数据目录
        self.data_path = self.plugin_path.parent.parent / "data" / "jmcomic"
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.download_path = self.data_path / "downloads"
        self.download_path.mkdir(parents=True, exist_ok=True)
        self.pdf_path = self.data_path / "pdf"
        self.pdf_path.mkdir(parents=True, exist_ok=True)
        
        # 初始化JM客户端
        await self._init_jm_client()
        
        logger.info("JMComic 插件初始化完成！")

    def _check_permission(self, event: AstrMessageEvent) -> bool:
        """检查权限（白名单）"""
        if not event.message_obj.group_id:
            return True
        
        if not self.enable_whitelist:
            return True
        
        group_id = str(event.message_obj.group_id)
        return group_id in [str(gid) for gid in self.group_whitelist]

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """检查是否是管理员"""
        return True

    async def _init_jm_client(self):
        """初始化JMComic客户端（支持登录）"""
        try:
            if jmcomic is None:
                logger.warning("jmcomic模块未找到，请执行 pip install jmcomic 安装依赖")
                return
            
            option = jmcomic.JmModuleConfig.option_class().default()
            option.dir_rule.base_dir = str(self.download_path)
            option.domain = self.jm_domain
            
            # 登录（如果配置了账号密码）
            if self.jm_username and self.jm_password:
                try:
                    client = option.build_jm_client()
                    client.login(self.jm_username, self.jm_password)
                    self.jm_client = client
                    logger.info(f"JMComic客户端初始化成功（已登录: {self.jm_username}）")
                except Exception as login_e:
                    logger.warning(f"JM登录失败: {login_e}，使用游客模式")
                    self.jm_client = option.build_jm_client()
            else:
                self.jm_client = option.build_jm_client()
                logger.info("JMComic客户端初始化成功（游客模式）")
                
        except Exception as e:
            logger.error(f"JMComic客户端初始化失败: {e}")

    def _scan_images(self, album_dir: Path) -> List[Path]:
        """扫描目录下的所有图片文件，按自然排序"""
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
        images = []
        
        if not album_dir.exists():
            return images
        
        for f in album_dir.rglob('*'):
            if f.suffix.lower() in image_extensions:
                images.append(f)
        
        images.sort(key=natural_sort_key)
        return images

    def _find_album_dir(self, album_id: str, album_name: str) -> Path:
        """查找漫画下载目录"""
        possible_names = [
            album_name,
            f"{album_id}_{album_name}",
            f"[{album_id}] {album_name}",
        ]
        
        for name in possible_names:
            dir_path = self.download_path / name
            if dir_path.exists() and dir_path.is_dir():
                return dir_path
        
        for item in self.download_path.iterdir():
            if item.is_dir() and album_id in item.name:
                return item
        
        safe_name = album_name[:20]
        for item in self.download_path.iterdir():
            if item.is_dir() and safe_name in item.name:
                return item
        
        return self.download_path / album_name

    def _generate_pdf(self, image_files: List[Path], pdf_file: Path) -> bool:
        """使用PIL生成PDF文件"""
        if not PIL_AVAILABLE:
            logger.error("Pillow未安装，无法生成PDF")
            return False
        
        if not image_files:
            logger.warning("没有找到图片文件")
            return False
        
        try:
            first_img = Image.open(str(image_files[0])).convert('RGB')
            
            other_imgs = []
            for f in image_files[1:]:
                try:
                    img = Image.open(str(f)).convert('RGB')
                    other_imgs.append(img)
                except Exception as e:
                    logger.warning(f"跳过损坏图片 {f.name}: {e}")
                    continue
            
            first_img.save(
                str(pdf_file),
                "PDF",
                resolution=100.0,
                save_all=True,
                append_images=other_imgs
            )
            
            first_img.close()
            for img in other_imgs:
                img.close()
            
            logger.info(f"PDF生成成功: {pdf_file.name}, 共{len(image_files)}张图片")
            return True
            
        except Exception as e:
            logger.error(f"生成PDF失败: {e}")
            return False

    def _download_album_sync(self, album_id: str) -> tuple:
        """同步下载漫画"""
        try:
            option = jmcomic.JmModuleConfig.option_class().default()
            option.dir_rule.base_dir = str(self.download_path)
            
            album = option.build_jm_client().get_album_detail(album_id)
            album_name = album.name
            
            jmcomic.download_album(album_id, option=option)
            
            album_dir = self._find_album_dir(album_id, album_name)
            image_files = self._scan_images(album_dir)
            
            logger.info(f"下载完成: {album_name}, 找到 {len(image_files)} 张图片")
            return (album, image_files, album_dir)
        except Exception as e:
            logger.error(f"下载失败: {e}")
            return (None, [], None)

    def _clean_files(self, clean_type: str = "all") -> dict:
        """安全清理本地文件"""
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
        
        stats = {
            'images_deleted': 0,
            'pdfs_deleted': 0,
            'space_freed': 0
        }
        
        if clean_type in ['all', 'images', 'img', '图片']:
            if self.download_path.exists():
                for f in self.download_path.rglob('*'):
                    if f.is_file() and f.suffix.lower() in image_extensions:
                        try:
                            file_size = f.stat().st_size
                            f.unlink()
                            stats['images_deleted'] += 1
                            stats['space_freed'] += file_size
                        except Exception as e:
                            logger.warning(f"删除图片失败 {f.name}: {e}")
            
            jm_default_dir = Path.cwd() / "jmcomic_download"
            if jm_default_dir.exists():
                for f in jm_default_dir.rglob('*'):
                    if f.is_file() and f.suffix.lower() in image_extensions:
                        try:
                            file_size = f.stat().st_size
                            f.unlink()
                            stats['images_deleted'] += 1
                            stats['space_freed'] += file_size
                        except Exception as e:
                            logger.warning(f"删除图片失败 {f.name}: {e}")
        
        if clean_type in ['all', 'pdf', 'PDF']:
            if self.pdf_path.exists():
                for f in self.pdf_path.glob('*.pdf'):
                    if f.is_file():
                        try:
                            file_size = f.stat().st_size
                            f.unlink()
                            stats['pdfs_deleted'] += 1
                            stats['space_freed'] += file_size
                        except Exception as e:
                            logger.warning(f"删除PDF失败 {f.name}: {e}")
        
        return stats

    @filter.command("jm搜索")
    async def jm_search(self, event: AstrMessageEvent):
        '''搜索禁漫漫画，用法：/jm搜索 [关键词] [页码]'''
        if not self._check_permission(event):
            yield event.plain_result("⚠️ 本群未开通JM漫画插件功能")
            return
            
        if not self.jm_client:
            yield event.plain_result("❌ JMComic客户端未初始化，请先执行 pip install jmcomic 安装依赖")
            return
            
        message = event.message_str.strip()
        parts = message.split()
        
        if len(parts) < 2:
            yield event.plain_result("📖 用法：/jm搜索 [关键词] [页码(可选)]\n示例：/jm搜索 全彩 1")
            return
            
        keyword = parts[1]
        page = 1
        if len(parts) >= 3 and parts[2].isdigit():
            page = int(parts[2])
        
        try:
            yield event.plain_result(f"🔍 正在搜索: {keyword} (第{page}页)...")
            
            search_result = self.jm_client.search_site(
                search_query=keyword,
                page=page
            )
            
            if not search_result or not search_result.content:
                yield event.plain_result(f"❌ 未找到相关漫画: {keyword}")
                return
            
            result_text = f"📚 搜索结果: {keyword} (第{page}/{search_result.page_count}页)\n\n"
            for i, (album_id, album_info) in enumerate(search_result.content[:10], 1):
                result_text += f"{i}. 【{album_id}】{album_info.get('name', '未知标题')}\n"
                result_text += f"   作者: {album_info.get('author', '未知')}\n"
                result_text += f"   分类: {album_info.get('category', {}).get('title', '')}\n\n"
            
            result_text += f"💡 查看详情: /jm详情 [ID]\n💡 下载漫画: /jm下载 [ID]"
            
            yield event.plain_result(result_text)
            
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            yield event.plain_result(f"❌ 搜索失败: {str(e)}")

    @filter.command("jm详情")
    async def jm_detail(self, event: AstrMessageEvent):
        '''查看漫画详情，用法：/jm详情 [漫画ID]'''
        if not self._check_permission(event):
            yield event.plain_result("⚠️ 本群未开通JM漫画插件功能")
            return
            
        if not self.jm_client:
            yield event.plain_result("❌ JMComic客户端未初始化，请先执行 pip install jmcomic 安装依赖")
            return
            
        message = event.message_str.strip()
        parts = message.split()
        
        if len(parts) < 2:
            yield event.plain_result("📖 用法：/jm详情 [漫画ID]\n示例：/jm详情 123456")
            return
            
        album_id = parts[1]
        
        try:
            yield event.plain_result(f"🔍 正在获取漫画详情: {album_id}...")
            
            album = self.jm_client.get_album_detail(album_id)
            
            if not album:
                yield event.plain_result(f"❌ 未找到漫画: {album_id}")
                return
            
            detail_text = f"📖 漫画详情\n"
            detail_text += f"━━━━━━━━━━━━━━\n"
            detail_text += f"📌 ID: {album.id}\n"
            detail_text += f"📕 标题: {album.name}\n"
            detail_text += f"✍️  作者: {album.author}\n"
            if album.tags:
                detail_text += f"🏷️  标签: {', '.join(album.tags[:10])}\n"
            episode_count = len(album.episode_list) if hasattr(album, 'episode_list') else 0
            detail_text += f"📚 章节数: {episode_count}话\n"
            detail_text += f"👁️  浏览量: {album.views}\n"
            detail_text += f"❤️  喜欢数: {album.likes}\n"
            
            if album.description:
                desc = album.description[:200]
                detail_text += f"\n📝 简介:\n{desc}..."
            
            detail_text += f"\n\n💡 下载漫画并生成PDF: /jm下载 {album_id}"
            
            yield event.plain_result(detail_text)
            
        except Exception as e:
            logger.error(f"获取详情失败: {e}")
            yield event.plain_result(f"❌ 获取详情失败: {str(e)}")

    @filter.command("jm下载")
    async def jm_download(self, event: AstrMessageEvent):
        '''下载漫画并生成PDF发送，用法：/jm下载 [漫画ID]'''
        if not self._check_permission(event):
            yield event.plain_result("⚠️ 本群未开通JM漫画插件功能")
            return
            
        if not self.jm_client:
            yield event.plain_result("❌ JMComic客户端未初始化，请先执行 pip install jmcomic 安装依赖")
            return
            
        if not PIL_AVAILABLE:
            yield event.plain_result("❌ Pillow未安装，请执行 pip install pillow")
            return
            
        message = event.message_str.strip()
        parts = message.split()
        
        if len(parts) < 2:
            yield event.plain_result("📖 用法：/jm下载 [漫画ID]\n示例：/jm下载 123456")
            return
            
        album_id = parts[1]
        
        try:
            yield event.plain_result(f"⬇️ 开始下载漫画: {album_id}...")
            
            loop = asyncio.get_event_loop()
            album, image_files, album_dir = await loop.run_in_executor(
                None,
                self._download_album_sync,
                album_id
            )
            
            if not album:
                yield event.plain_result("❌ 下载失败，未找到漫画")
                return
            
            if not image_files:
                yield event.plain_result("❌ 未找到图片文件")
                return
            
            album_name = album.name
            safe_name = "".join(c for c in album_name if c.isalnum() or c in (' ', '-', '_')).strip()
            pdf_file = self.pdf_path / f"{album_id}_{safe_name[:30]}.pdf"
            
            pdf_success = await loop.run_in_executor(
                None,
                self._generate_pdf,
                image_files,
                pdf_file
            )
            
            if not pdf_success or not pdf_file.exists():
                yield event.plain_result("❌ PDF生成失败")
                return
            
            # ✅ 发送PDF文件 - 官方标准API：yield event.chain_result([Comp.File(...)])
            try:
                yield event.chain_result([
                    Comp.File(
                        file=str(pdf_file),
                        name=os.path.basename(str(pdf_file))
                    )
                ])
                
                if self.clean_temp_files:
                    try:
                        import shutil
                        if album_dir and album_dir.exists():
                            shutil.rmtree(album_dir)
                    except Exception as e:
                        logger.warning(f"清理临时文件失败: {e}")
            except Exception as e:
                logger.error(f"发送PDF失败: {e}")
                yield event.plain_result(f"❌ 发送PDF失败: {str(e)}")
                
        except Exception as e:
            logger.error(f"下载失败: {e}")
            yield event.plain_result(f"❌ 下载失败: {str(e)}")

    @filter.command("jm排行")
    async def jm_ranking(self, event: AstrMessageEvent):
        '''查看排行榜，用法：/jm排行 [日/周/月]'''
        if not self._check_permission(event):
            yield event.plain_result("⚠️ 本群未开通JM漫画插件功能")
            return
            
        if not self.jm_client:
            yield event.plain_result("❌ JMComic客户端未初始化，请先执行 pip install jmcomic 安装依赖")
            return
            
        message = event.message_str.strip()
        parts = message.split()
        
        rank_type = "周"
        rank_method = "week_ranking"
        
        if len(parts) >= 2:
            if parts[1] in ["日", "day"]:
                rank_type = "日"
                rank_method = "day_ranking"
            elif parts[1] in ["月", "month"]:
                rank_type = "月"
                rank_method = "month_ranking"
        
        try:
            yield event.plain_result(f"📊 正在获取{rank_type}排行榜...")
            
            method = getattr(self.jm_client, rank_method)
            result = method(1)
            
            if not result or not result.content:
                yield event.plain_result("❌ 获取排行榜失败")
                return
            
            rank_text = f"🏆 {rank_type}排行榜 TOP15\n"
            rank_text += "━━━━━━━━━━━━━━\n"
            for i, (album_id, album_info) in enumerate(result.content[:15], 1):
                name = album_info.get('name', '未知')
                name = name[:35] + "..." if len(name) > 35 else name
                rank_text += f"{i:2d}. 【{album_id}】{name}\n"
            
            rank_text += "\n💡 查看详情: /jm详情 [ID]"
            yield event.plain_result(rank_text)
            
        except Exception as e:
            logger.error(f"获取排行榜失败: {e}")
            yield event.plain_result(f"❌ 获取排行榜失败: {str(e)}")

    @filter.command("jm清理")
    async def jm_clean(self, event: AstrMessageEvent):
        '''清理本地文件，用法：/jm清理 [类型]'''
        if not self._is_admin(event):
            yield event.plain_result("❌ 只有管理员可以使用此命令")
            return
            
        message = event.message_str.strip()
        parts = message.split()
        
        clean_type = "all"
        type_name = "全部"
        
        if len(parts) >= 2:
            arg = parts[1].lower()
            if arg in ["图片", "img", "image", "images"]:
                clean_type = "images"
                type_name = "图片"
            elif arg in ["pdf", "PDF"]:
                clean_type = "pdf"
                type_name = "PDF"
            else:
                clean_type = "all"
                type_name = "全部"
        
        try:
            yield event.plain_result(f"🧹 正在清理{type_name}文件...")
            
            loop = asyncio.get_event_loop()
            stats = await loop.run_in_executor(
                None,
                self._clean_files,
                clean_type
            )
            
            space_mb = stats['space_freed'] / 1024 / 1024
            
            result = f"""🧹 清理完成！
├─ 删除图片：{stats['images_deleted']} 个
├─ 删除PDF：{stats['pdfs_deleted']} 个
└─ 释放空间：{space_mb:.2f} MB"""
            
            yield event.plain_result(result)
            
        except Exception as e:
            logger.error(f"清理失败: {e}")
            yield event.plain_result(f"❌ 清理失败: {str(e)}")

    @filter.command("jm帮助")
    async def jm_help(self, event: AstrMessageEvent):
        '''显示JMComic插件帮助'''
        help_text = """📚 JMComic 禁漫漫画插件帮助
━━━━━━━━━━━━━━
🔍 搜索漫画
  /jm搜索 [关键词] [页码]
  示例: /jm搜索 全彩 1

📖 查看详情
  /jm详情 [漫画ID]
  示例: /jm详情 123456

📄 下载并生成PDF
  /jm下载 [漫画ID]
  示例: /jm下载 123456
  (无尺寸限制，支持所有格式，支持重复下载)

🏆 查看排行
  /jm排行 [日/周/月]
  示例: /jm排行 周

👑 管理员命令
  /jm清理 [全部/图片/PDF]

⚙️  配置说明
  白名单、账号密码等配置
  请在 AstrBot WebUI 中设置

❓ 显示帮助
  /jm帮助
━━━━━━━━━━━━━━
💡 提示: 使用PIL生成PDF，无尺寸限制！"""
        yield event.plain_result(help_text)

    async def terminate(self):
        '''插件卸载时调用'''
        logger.info("JMComic 插件已卸载")
