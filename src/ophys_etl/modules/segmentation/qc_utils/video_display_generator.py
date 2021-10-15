from pathlib import Path
import tempfile

from ophys_etl.modules.segmentation.qc_utils import video_utils


class VideoDisplayGenerator(object):
    """
    This is a helper class designed to serve up thumbnail videos
    in a way that they can be displayed in Jupyter notebooks.

    In order to display videos without embedding them, the videos
    have to be in a path relative to the notebook's current working
    directory. Rather than actually write our thumbnails there,
    we will create a separate temp dir with symlinks to the actual
    thumbnails. This class will handle that process. It will also
    clean up the symlinks behind itself on deletion.

    The constructor for this class takes no arguments. It just
    creates a temporary directory that is a sub-directory of
    the notebook's working directory. The only method any user
    should care about is

    my_video_display_generator.display_video()

    which returns a dict of parameters suitable to be passed to
    IPython.display.Video, i.e.

    Video(**my_video_display_generator.display_video(thumbnail))

    will cause a thumbnail video to be displayed in the notebook.
    """
    def __init__(self):
        self.this_dir = Path('.').absolute()
        this_tmp = self.this_dir / 'tmp'
        if not this_tmp.is_dir():
            this_tmp.mkdir()
        tmp_prefix = 'temp_thumbnail_video_symlinks_'
        self.tmp_dir = Path(tempfile.mkdtemp(dir=this_tmp,
                                             prefix=tmp_prefix))

        # put a dummy file in the directory so that it doesn't get
        # deleted by self.clean_tmp
        dummy_name = self.tmp_dir / 'dummy.txt'
        with open(dummy_name, 'w') as out_file:
            out_file.write('# empty')
        self.files_written = [dummy_name]
        self.clean_tmp()

    def clean_tmp(self):
        """
        Scan through self.tmp_dir, cleaning out empty thumbnail dirs that
        have accumulated while running this notebook. We need to do this
        here because, as this class is used, .nfs files get placed in the
        temp dir. These are still in use when the destructor from this
        class gets called, making it impossible for us to clean up tmp
        upon deconstruction of this class.
        """
        parent = self.tmp_dir.parent
        contents = [name for name
                    in parent.rglob('temp_thumbnail_video_symlinks_*')]
        for dirname in contents:
            if dirname == self.tmp_dir:
                continue
            if dirname.is_dir():
                sub_contents = [fname for fname in dirname.iterdir()]
                # check for broken symlinks and clean them up
                for ii in range(len(sub_contents)-1, -1, -1):
                    this_path = sub_contents[ii]
                    if this_path.is_symlink():
                        if not this_path.resolve().exists():
                            this_path.unlink()
                            sub_contents.pop(ii)
                if len(sub_contents) == 0:
                    dirname.rmdir()

    def __del__(self):
        """
        Automatically delete all of the symlinks that were written.
        """
        for f_path in self.files_written:
            if f_path.exists():
                f_path.unlink()
        self.clean_tmp()

    def display_video(self,
                      thumbnail: video_utils.ThumbnailVideo,
                      width: int = 512,
                      height: int = 512) -> dict:
        """
        Display a video in this notebook. As a part of displaying the video,
        a symlink to the actual video is created somewhere under the directory
        of this notebook.

        Parameters
        ----------
        thumbnail: video_utils.ThumbnailVideo

        width: int
            The width of the video as you want it to appear in the notebook
            (default = 512)

        height: int
            The height of the video as you want it to appear in the notebook

        Returns
        -------
        params: dict
            The parameters that need to be passed as kwargs to
            IPython.display.Video to get the video to appear in the
            notebook, specifically

           'data' -- the path to a symlink to the video *relative
           to the current working directory*

           'width' -- as passed to this method

           'height' -- as passed to this method

           'embed' -- False; so that the video is not embedded in
           the notebook
        """
        # in case another instance of this notebook accidentally
        # deleted this notebook's tmpdir
        if not self.tmp_dir.exists():
            self.tmp_dir.mkdir(parents=True)

        tmp_path = Path(tempfile.mkstemp(dir=self.tmp_dir,
                                         prefix='thumbnail_symlink_',
                                         suffix='.mp4')[1])
        tmp_path.unlink()  # because mkstemp creates the file
        tmp_path.symlink_to(thumbnail.video_path.resolve())
        plot_path = tmp_path.relative_to(self.this_dir.resolve())
        self.files_written.append(plot_path)
        return {'data': plot_path,
                'width': width,
                'height': height,
                'embed': False}