'''Models.'''
from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

import numpy

import chainer
import chainer.functions as F
import chainer.links as L

import wavenet.utils as utils


class MaskedConvolution2D(L.Convolution2D):
    def __init__(self, *args, mask='B', **kwargs):
        super(MaskedConvolution2D, self).__init__(
            *args, **kwargs
        )

        kh, kw = (self.ksize, ) * 2
        pre_mask = self.xp.ones([kh, kw]).astype('f')
        yc, xc = kh // 2, kw // 2

        pre_mask[yc+1:, :] = 0.0
        pre_mask[yc:, xc+1:] = 0.0
        if mask == 'A':
            pre_mask[yc, xc] = 0.0

        self.add_persistent(
            'mask', self.xp.broadcast_to(pre_mask, self.W.shape))

    def __call__(self, x):
        if self.has_uninitialized_params:
            with chainer.cuda.get_device(self._device_id):
                self._initialize_params(x.shape[1])

        return chainer.functions.connection.convolution_2d.convolution_2d(
            x, self.W * self.mask, self.b, self.stride, self.pad, self.use_cudnn,
            deterministic=self.deterministic)


class ResidualBlock(chainer.Chain):
    def __init__(self, in_channels, nobias=False):
        super(ResidualBlock, self).__init__(
            conv1=L.Convolution2D(in_channels, in_channels // 2, 1, nobias=nobias),
            conv2=MaskedConvolution2D(in_channels // 2, in_channels // 2, 3, pad=1, nobias=nobias),
            conv3=L.Convolution2D(in_channels // 2, in_channels, 1, nobias=nobias)
        )

    def __call__(self, x):
        h = F.relu(self.conv1(x))
        h = F.relu(self.conv2(h))
        h = self.conv3(h)

        return F.relu(x + h)


class ResidualBlockList(chainer.ChainList):
    def __init__(self, block_num, hidden_dims, mask='B', nobias=False):
        blocks = [ResidualBlock(hidden_dims, nobias=nobias) for _ in range(block_num)]
        super(ResidualBlockList, self).__init__(*blocks)

    def __call__(self, x):
        h = x
        for block in self:
            h = block(h)
        return h


class PixelCNN(chainer.Chain):
    def __init__(self, in_channels, hidden_dims, block_num, out_hidden_dims, nobias=False):
        super(PixelCNN, self).__init__(
            conv1=MaskedConvolution2D(in_channels, hidden_dims, 7, pad=3, mask='A', nobias=nobias),
            blocks=ResidualBlockList(block_num, hidden_dims, nobias=nobias),
            conv2=L.Convolution2D(hidden_dims, out_hidden_dims, 1, nobias=nobias),
            conv3=L.Convolution2D(out_hidden_dims, out_hidden_dims, 1, nobias=nobias),
            conv4=L.Convolution2D(out_hidden_dims, 1, 1, nobias=nobias)
        )

    def __call__(self, x):
        h = F.relu(self.conv1(x))
        h = self.blocks(h)
        h = F.relu(self.conv2(h))
        h = F.relu(self.conv3(h))
        h = self.conv4(h)

        return h


# TODO: rename class
class Classifier(chainer.Chain):
     def __init__(self, predictor):
         super(Classifier, self).__init__(predictor=predictor)

     def __call__(self, x):
         y = self.predictor(x)

         # TODO: replace hard-coded reshaping
         nll = F.sigmoid_cross_entropy(y, F.cast(x, 'i')) / x.size
         chainer.report({'nll': nll}, self)
         return nll
